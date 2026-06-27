"""
server/graph/relevance_tagger.py
--------------------------------
관련성 태깅 (RP-D1) — Haiku 가 댓글/게시글이 '카드 기능 반응(aspect)'인지 분류해
각 item 에 ``_relevant``(bool) 를 설정한다. 컷 우선순위(RP-D3, reaction_analysis_node)에 쓰인다.

설계 근거(PoC 검증분)
  - 출력은 "관련 aspect_id / none" 1개만 → 출력 토큰 최소(싸고 빠름).
  - 대댓글은 ``[부모] … ↳ [답글] …`` 로 맥락을 함께 보여주되, 프롬프트가 **답글만 판정**
    하도록 지시(부모 맥락 전가 착시 억제 — sonnet·haiku 모두 통제 확인).
  - 검증 엔진은 CLI(구독·과금 없음), 운영은 API(haiku) 선택.

실패 정책: 배치 호출 실패 시 그 배치는 보수적으로 ``_relevant=True``(분석 누락 방지).
"""
from __future__ import annotations

import logging
from typing import Any

from server.cache_ttl import get_ttl_hours
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)

_HAIKU = "claude-haiku-4-5-20251001"
_SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {"type": "string"}}},
    "required": ["labels"],
}

# ── per-item 관련성 캐시 (RP-D1) ──────────────────────────────────────────────
# item 단위 캐시: 라벨은 (분류 텍스트 + 프롬프트/모델)에만 의존하고 시간 드리프트가 없어
# 배치 구성이 바뀌어도 안정 적중한다(community fetch 캐시가 URL 단위인 것과 동일 논리).
# 원시 label(aspect_id/none)을 저장해 _relevant 재도출·감사·재집계에 쓴다.
# 실패 폴백(_relevant=True)은 **저장하지 않는다**(일시 장애가 캐시를 오염시키지 않도록).
_CACHE_AGENT_ID = "relevance_tagger"
_LABEL_SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string"}},
    "required": ["label"],
}
_TTL_H = get_ttl_hours("relevance_tag_hours", 720)  # cache_ttls.yaml


def _sys_prompt(aspects: list[dict]) -> str:
    lines = [f"- {a.get('aspect_id')}: {a.get('label')}" for a in aspects]
    return (
        "너는 '카드 상품 고객 반응' 분류기다. 각 항목이 아래 카드 기능(aspect) 중 하나에 대한 "
        "**소비자의 의견·평가·경험(반응)**이면 그 aspect_id를, 아니면 정확히 'none'을 출력하라.\n"
        "none: 단순 사실/정보, 질문, 인사·감사, 영상/채널 잡담, 여행·음식·날씨 후기 등 "
        "카드 기능 반응이 아닌 글.\n"
        "대댓글은 '[부모] … ↳ [답글] …' 형태로 주어진다. **판정 대상은 [답글]이며 [부모]는 "
        "맥락 참고용. 답글 자체가 반응이 아니면(단순 동의·잡담·ㅋㅋ) none**(부모가 관련 있어도).\n"
        "출력은 입력 개수와 같은 길이의 labels 배열만. 설명 금지.\n\n"
        "aspect 목록:\n" + "\n".join(lines)
    )


def _build_analyzer(engine: str, model: str, system_prompt: str):
    if engine == "api":
        from server.llm.claude_api_analyzer import ClaudeApiAnalyzer
        return ClaudeApiAnalyzer(model=model or _HAIKU, system_prompt=system_prompt,
                                 max_tokens=1024)
    from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer
    return ClaudeCodeCliAnalyzer(model=model or "claude-sonnet-4-6",
                                 system_prompt=system_prompt, timeout=180)


def tag_relevance(items: list[dict], aspects: list[dict], *, engine: str = "cli",
                  model: str = "", batch: int = 20,
                  logger: logging.Logger | None = None) -> None:
    """items 각각에 ``_relevant``(bool) 를 in-place 설정한다."""
    if not items:
        return
    if not aspects:
        for it in items:
            it["_relevant"] = True
        return
    valid_ids = {a.get("aspect_id") for a in aspects}
    # 대댓글 맥락: thread_id → 최상위(부모) 텍스트
    parent: dict[str, str] = {}
    for it in items:
        if not it.get("is_reply"):
            parent[it.get("thread_id", "")] = it.get("text", "")

    def disp(it: dict) -> str:
        t = it.get("text", "")
        if it.get("is_reply"):
            p = parent.get(it.get("thread_id", ""), "")
            return f"[부모] {p[:150]} ↳ [답글] {t}" if p else t
        return t

    system_prompt = _sys_prompt(aspects)
    analyzer = _build_analyzer(engine, model, system_prompt)
    context = make_cache_context(
        agent_id=_CACHE_AGENT_ID,
        model=getattr(analyzer, "model", engine),
        system_prompt=system_prompt,
        output_schema=_LABEL_SCHEMA,
    )

    def _apply(it: dict, lab: str) -> None:
        it["_relevance_label"] = lab
        it["_relevant"] = lab in valid_ids

    # ── 1) 캐시 조회 — 적중분은 라벨 적용, 미스만 분류 대상(todo) ──────────────
    todo: list[dict] = []
    for it in items:
        it["_ci"] = {"text": disp(it)[:300]}        # 분류에 실제 투입되는 문자열
        hit = load_agent_output(agent_id=_CACHE_AGENT_ID, cache_input=it["_ci"],
                                context=context, output_schema=_LABEL_SCHEMA,
                                ttl_hours=_TTL_H)
        if hit is not None and isinstance(hit.get("label"), str):
            _apply(it, hit["label"])
            it.pop("_ci", None)
        else:
            todo.append(it)
    n_hit = len(items) - len(todo)

    # ── 2) 미스만 배치 분류 → 성공 라벨만 캐시 저장(실패 폴백은 미저장) ─────────
    for s in range(0, len(todo), batch):
        chunk = todo[s:s + batch]
        prompt = ("다음 항목들을 분류하라(순서대로 labels 배열):\n"
                  + "\n".join(f"{j+1}. {disp(it)[:300]}" for j, it in enumerate(chunk)))
        try:
            out = analyzer.call_with_schema(prompt, _SCHEMA)
            labels = out.get("labels", [])
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger.warning("relevance 배치 실패(@%d) — 보수적 통과: %s", s, str(exc)[:120])
            labels = []
        for j, it in enumerate(chunk):
            ci = it.pop("_ci", None)
            if not labels:                      # 배치 실패 → 보수적 관련(분석 누락 방지), 미저장
                it["_relevant"] = True
                continue
            lab = labels[j] if j < len(labels) else "none"
            _apply(it, lab)
            if ci is not None:
                store_agent_output(agent_id=_CACHE_AGENT_ID, cache_input=ci,
                                   context=context, output={"label": lab})

    n_rel = sum(1 for it in items if it.get("_relevant"))
    if logger:
        logger.info("relevance 태깅: 캐시적중 %d · 신규분류 %d · 관련 %d/%d (engine=%s)",
                    n_hit, len(todo), n_rel, len(items), engine)
