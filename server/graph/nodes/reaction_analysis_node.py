"""
server/graph/nodes/reaction_analysis_node.py (v0.13 — reaction_insight 시리즈)
-------------------------------------------------------------------------------
사용자 반응 ABSA 노드 — 채널 2종 원자료를 aspect_codebook 기반 7-tuple 로 분해.
설계: docs/design/reaction_insight_node_design.md §5 (RI-D5 어댑터 = CLI)

책임 분리 (CM-D1 사상)
----------------------
- 코드: 채널 2종 입력 조립(dedup·candidate 연관) · LLM 출력 가드(코드북 외 aspect ·
  입력에 없는 source_url · 비실존 quote 제거) · 표본 메타(AP-3) 집계.
- LLM (ClaudeCodeCliAnalyzer, candidate당 1회): 7-tuple 추출 — 분류·발췌만.

read keys
---------
- selected_comments / collected_videos  (youtube_reaction_collection 산출)
- community_posts                       (community_collection 산출 → channel="community")
- blog_posts                            (blog_collection 산출 → channel="blog"; 미배선 시 빈 리스트)
- domain_taxonomy.report_config["reaction_insight"].aspect_codebook
- selected_purposes (게이트)

write keys
----------
- reaction_analysis : {candidate_id: {"tuples": [7-tuple+is_suggestion],
                       "channel_counts": {"youtube": n, "community": n, "blog": n},
                       "sample_size": int, "collected_at": ISO8601,
                       "dropped_by_guard": int}}
- agent_steps / errors (누적 reducer)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from server.config import AGENTS_DIR
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.graph.nodes.feature_url_mapper_node import _load_json, _load_text

logger = logging.getLogger(__name__)

REPORT_TYPE   = "reaction_insight"
_LLM_AGENT_ID = "reaction_analysis"
_LLM_TIMEOUT_SEC = 600     # 댓글 대량 입력 — CLI 여유 timeout

_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """quote 실존 검증용 정규화 — 공백 제거 (LLM 의 공백 변형 허용)."""
    return _WS_RE.sub("", text or "")


# ─── 코드 파트: 입력 조립 (순수 함수) ────────────────────────────────────────

def build_absa_inputs(state: dict) -> dict[str, list[dict]]:
    """candidate_id → ABSA 입력 items (채널 2종 통합·dedup).

    youtube: 댓글 1건 = item 1건 (source_url = 영상 watch URL).
    community: 게시글 1건 = item 1건 (제목 + 본문 발췌).
    """
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return {}

    video_url = {v.get("video_id", ""): v.get("url", "")
                 for v in state.get("collected_videos") or []}

    items: dict[str, list[dict]] = {}
    seen: dict[str, set[str]] = {}

    for c in state.get("selected_comments") or []:
        cid = c.get("candidate_id", "")
        text = (c.get("text") or "").strip()
        if not cid or not text:
            continue
        key = _norm(text)[:200]
        if key in seen.setdefault(cid, set()):
            continue
        seen[cid].add(key)
        items.setdefault(cid, []).append({
            "channel":    "youtube",
            "source_url": video_url.get(c.get("video_id", ""), "") or
                          f"https://www.youtube.com/watch?v={c.get('video_id', '')}",
            "posted_at":  c.get("published_at", ""),
            "text":       text,
        })

    # community_posts → channel="community", blog_posts → channel="blog".
    # blog_posts 는 blog_collection_node(미배선/휴면) 산출이라 현재는 빈 리스트.
    for state_key, channel in (("community_posts", "community"),
                               ("blog_posts", "blog")):
        for p in state.get(state_key) or []:
            cid = p.get("candidate_id", "")
            body = (p.get("body_excerpt") or "").strip()
            if not cid or not body:
                continue
            text = f"{p.get('title', '')}\n{body}".strip()
            key = _norm(text)[:200]
            if key in seen.setdefault(cid, set()):
                continue
            seen[cid].add(key)
            items.setdefault(cid, []).append({
                "channel":    channel,
                "source_url": p.get("url", ""),
                "posted_at":  p.get("published_at", ""),
                "text":       text,
            })

    return items


def _aspect_ids(state: dict) -> list[dict]:
    """taxonomy aspect_codebook → LLM 입력용 [{aspect_id, label, definition}]."""
    entry = ((state.get("domain_taxonomy") or {}).get("report_config") or {}) \
        .get(REPORT_TYPE) or {}
    aspects = []
    for a in entry.get("aspect_codebook") or []:
        if isinstance(a, dict) and a.get("aspect_id"):
            aspects.append({
                "aspect_id":  a["aspect_id"],
                "label":      a.get("label", a["aspect_id"]),
                "definition": a.get("definition", ""),
            })
        elif isinstance(a, str):
            aspects.append({"aspect_id": a, "label": a, "definition": ""})
    return aspects


# ─── 코드 파트: LLM 출력 가드 ────────────────────────────────────────────────

def sanitize_tuples(tuples: list[dict], items: list[dict],
                    valid_aspects: set[str]) -> tuple[list[dict], int]:
    """환각 제거 — 코드북 외 aspect · 입력에 없는 source_url · 비실존 quote.

    반환: (정제 tuple 목록, 제거 건수)
    """
    valid_urls = {it["source_url"] for it in items}
    corpus = _norm(" ".join(it["text"] for it in items))
    kept: list[dict] = []
    dropped = 0
    for t in tuples:
        if t.get("aspect") not in valid_aspects \
                or t.get("source_url") not in valid_urls \
                or not t.get("quote") \
                or _norm(t["quote"]) not in corpus:
            dropped += 1
            continue
        kept.append(t)
    return kept, dropped


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def reaction_analysis_node(
    state: DomainAnalysisState, config: dict | None = None, analyzer=None
) -> dict:
    """채널 2종 반응 원자료 → ABSA 7-tuple (candidate당 LLM 1회 + 가드)."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_analysis",
                         detail="사용자 반응 ABSA 분석")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    inputs = build_absa_inputs(dict(state))
    if not inputs:
        return {"agent_steps": [_step("skipped", started_at)]}

    aspects = _aspect_ids(dict(state))
    if not aspects:
        return _error_out(started_at,
                          "aspect_codebook 이 비어 있습니다 — domain_taxonomy 확인 필요.")
    valid_aspects = {a["aspect_id"] for a in aspects}

    system_prompt = _load_text(AGENTS_DIR / _LLM_AGENT_ID / "system_prompt_kr.md")
    output_schema = _load_json(AGENTS_DIR / _LLM_AGENT_ID / "output.schema.json")
    if system_prompt is None or output_schema is None:
        return _error_out(started_at, f"agents/{_LLM_AGENT_ID}/ prompt·schema 로드 실패.")

    if analyzer is None:
        from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer  # 지연 import
        analyzer = ClaudeCodeCliAnalyzer(system_prompt=system_prompt,
                                         timeout=_LLM_TIMEOUT_SEC)
    context = make_cache_context(
        agent_id=_LLM_AGENT_ID,
        model=getattr(analyzer, "model", "claude_cli"),
        system_prompt=system_prompt,
        output_schema=output_schema,
    )

    errors: list[dict] = []
    reaction_analysis: dict[str, dict] = {}
    name_by_cid = {
        p.get("candidate_id", ""): p.get("product_name", "")
        for p in state.get("product_profiles") or []
    }

    # candidate 순차 처리 (CLI 어댑터 — 동시 실행 이점 없음)
    for cid in sorted(inputs):
        items = inputs[cid]
        payload = {
            "candidate_id":   cid,
            "candidate_name": name_by_cid.get(cid, cid),
            "aspects":        aspects,
            "items":          items,
        }
        cache_input = {
            "candidate_id": cid,
            "aspect_ids":   sorted(valid_aspects),
            "items_sha":    [_norm(it["text"])[:64] for it in items],
        }
        try:
            llm_out = load_agent_output(
                agent_id=_LLM_AGENT_ID, cache_input=cache_input,
                context=context, output_schema=output_schema, logger=logger)
            if llm_out is None:
                prompt = ("다음 사용자 반응에서 aspect 별 의견을 추출하라.\n\n```json\n"
                          + json.dumps(payload, ensure_ascii=False) + "\n```")
                llm_out = analyzer.call_with_schema(prompt, output_schema)
                store_agent_output(
                    agent_id=_LLM_AGENT_ID, cache_input=cache_input,
                    context=context, output=llm_out, logger=logger)

            tuples, dropped = sanitize_tuples(
                llm_out.get("tuples", []), items, valid_aspects)
            reaction_analysis[cid] = {
                "tuples": tuples,
                "channel_counts": {
                    "youtube":   sum(1 for it in items if it["channel"] == "youtube"),
                    "community": sum(1 for it in items if it["channel"] == "community"),
                    "blog":      sum(1 for it in items if it["channel"] == "blog"),
                },
                "sample_size":      len(items),          # AP-3 — 표본 크기 의무
                "collected_at":     started_at,          # AP-3 — 수집 시점 의무
                "dropped_by_guard": dropped,
            }
            if dropped:
                logger.warning("reaction_analysis: %s 가드 제거 %d건", cid, dropped)
        except Exception as exc:  # noqa: BLE001 — candidate 단위 부분 실패 (§7)
            errors.append({
                "node": "reaction_analysis_node",
                "error": f"candidate={cid}: {type(exc).__name__}: {str(exc)[:200]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    step = _step("completed", started_at)
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패"
    total = sum(len(v["tuples"]) for v in reaction_analysis.values())
    logger.info("reaction_analysis: %d candidates · tuple %d건 (부분실패 %d)",
                len(reaction_analysis), total, len(errors))

    out: dict = {"reaction_analysis": reaction_analysis, "agent_steps": [step]}
    if errors:
        out["errors"] = errors
    return out


def _step(status: str, started_at: str, error_message: str = "") -> AgentStep:
    step: AgentStep = {
        "step_name":   "ReactionAnalysis",
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_message:
        step["error_message"] = error_message
    return step


def _error_out(started_at: str, message: str) -> dict:
    logger.error("reaction_analysis_node: %s", message)
    return {
        "errors": [{"node": "reaction_analysis_node", "error": message,
                    "timestamp": datetime.now(timezone.utc).isoformat()}],
        "agent_steps": [_step("failed", started_at, message)],
    }
