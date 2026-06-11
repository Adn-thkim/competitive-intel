"""
server/graph/nodes/reaction_insight_node.py (v0.13 — 구현)
-----------------------------------------------------------
`reaction_insight` 리포트 노드 — ABSA 7-tuple 집계 → 고객 반응 인사이트 envelope.
설계: docs/design/reaction_insight_node_design.md §6 (RI-D6·RI-D7)

책임 분리 (CM-D1 사상)
----------------------
- 코드 (결정론): aspect × polarity 매트릭스 · 채널 가중 sentiment(RI-D7: youtube 1.0 /
  community 0.9) · 대표 quote 선정 · suggestion 분리 · 월별 timeline · 루브릭 코드
  채점(RI-D6) · LLM 서술의 환각 aspect 가드.
- LLM (ClaudeCodeCliAnalyzer, 리포트당 1회): aspect 별 인사이트 서술·종합 요지만.
  실패 시 집계 전용 degrade (CM-D5 패턴).

read keys
---------
- reaction_analysis  (reaction_analysis_node 산출 — candidate별 tuples + AP-3 메타)
- domain_taxonomy.report_config["reaction_insight"]  (active·label·categories·aspect_codebook)
- own_product.product_id

write keys
----------
- report_outputs["reaction_insight"]  (merge reducer — 자기 키만 반환)

루브릭 코드 채점 (RI-D6 — CM-D6 패턴)
--------------------------------------
  2점: tuple 0건 (aspect 분해 실패)
  3점: 7-tuple ≥ 1건 (단일 채널)
  4점: 3점 + 채널 2종(youtube·community) 모두 tuple 보유 (cross-validation)
  5점: 4점 + posted_at 보유 tuple ≥ 50% (시점 분리 뷰 성립) + suggestion ≥ 1건 분리
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from server.config import AGENTS_DIR
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.state import DomainAnalysisState
from server.graph.nodes._report_node_common import (
    build_report_envelope,
    get_report_entry,
    is_report_active,
    make_completed_step,
    make_error_result,
    make_skip_result,
)
from server.graph.nodes.feature_url_mapper_node import _load_json, _load_text

logger = logging.getLogger(__name__)

REPORT_TYPE    = "reaction_insight"
RUBRIC_VERSION = "report_taxonomy.md §2-2 (2026-05)"

_LLM_TIMEOUT_SEC = 300

# RI-D7 — 채널 가중치 (사용자 확정 2026-06-06; 2026-06-11 blog 분리 — 블로그·커뮤니티 동일 0.9)
CHANNEL_WEIGHTS = {"youtube": 1.0, "community": 0.9, "blog": 0.9}
_POL_SIGN = {"positive": 1, "negative": -1, "neutral": 0}


# ─── 코드 파트: 집계 (결정론) ────────────────────────────────────────────────

def build_aspect_matrix(reaction_analysis: dict) -> dict:
    """aspect × candidate 집계 — 극성 건수 + 채널 가중 sentiment(-1~+1)."""
    matrix: dict[str, dict[str, dict]] = defaultdict(dict)
    for cid in sorted(reaction_analysis):
        by_aspect: dict[str, list[dict]] = defaultdict(list)
        for t in reaction_analysis[cid].get("tuples", []):
            by_aspect[t["aspect"]].append(t)
        for aspect, tuples in by_aspect.items():
            num = den = 0.0
            counts = {"positive": 0, "negative": 0, "neutral": 0}
            for t in tuples:
                counts[t["polarity"]] = counts.get(t["polarity"], 0) + 1
                w = CHANNEL_WEIGHTS.get(t.get("channel", ""), 1.0)
                intensity = int(t.get("intensity", 1) or 1)
                num += _POL_SIGN.get(t["polarity"], 0) * intensity * w
                den += intensity * w
            matrix[aspect][cid] = {
                **counts,
                "tuple_count":        len(tuples),
                "weighted_sentiment": round(num / den, 3) if den else 0.0,
            }
    return {a: matrix[a] for a in sorted(matrix)}


def select_top_quotes(reaction_analysis: dict, per_bucket: int = 1) -> list[dict]:
    """aspect × 극성(pos/neg)별 대표 quote — intensity·채널 가중 상위 (결정론)."""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for cid in sorted(reaction_analysis):
        for t in reaction_analysis[cid].get("tuples", []):
            if t["polarity"] == "neutral":
                continue
            buckets[(t["aspect"], t["polarity"])].append({**t, "candidate_id": cid})
    quotes: list[dict] = []
    for key in sorted(buckets):
        ranked = sorted(buckets[key], key=lambda t: (
            -int(t.get("intensity", 1) or 1),
            -CHANNEL_WEIGHTS.get(t.get("channel", ""), 1.0),
            t.get("quote", "")))
        for t in ranked[:per_bucket]:
            quotes.append({
                "aspect": t["aspect"], "polarity": t["polarity"],
                "quote": t["quote"], "candidate_id": t["candidate_id"],
                "channel": t["channel"], "source_url": t["source_url"],
                "posted_at": t.get("posted_at", ""),
                "intensity": int(t.get("intensity", 1) or 1),
            })
    return quotes


def build_suggestions(reaction_analysis: dict) -> list[dict]:
    """is_suggestion tuple 분리 (5점 요건 — product_dev 후보)."""
    out = []
    for cid in sorted(reaction_analysis):
        for t in reaction_analysis[cid].get("tuples", []):
            if t.get("is_suggestion"):
                out.append({
                    "candidate_id": cid, "aspect": t["aspect"], "quote": t["quote"],
                    "channel": t["channel"], "source_url": t["source_url"],
                    "posted_at": t.get("posted_at", ""),
                })
    return out


def build_timeline(reaction_analysis: dict) -> dict:
    """candidate별 월별(YYYY-MM) tuple 수·평균 sentiment — 시점 분리 뷰.

    v0.13.2 — 전체 합산에서 candidate별 분리로 변경 (UI 드롭다운 필터 요건).
    구조: {candidate_id: {YYYY-MM: {"count", "avg_sentiment"}}} (posted_at 보유분만)
    """
    out: dict[str, dict] = {}
    for cid in sorted(reaction_analysis):
        by_month: dict[str, list[float]] = defaultdict(list)
        for t in reaction_analysis[cid].get("tuples", []):
            month = (t.get("posted_at") or "")[:7]
            if len(month) == 7:
                by_month[month].append(float(_POL_SIGN.get(t["polarity"], 0)))
        out[cid] = {
            m: {"count": len(v), "avg_sentiment": round(sum(v) / len(v), 3)}
            for m, v in sorted(by_month.items())
        }
    return out


def compute_rubric(reaction_analysis: dict, suggestions: list) -> tuple[int, str]:
    """RI-D6 — 루브릭 코드 결정론 채점 (모듈 docstring 규칙)."""
    all_tuples = [t for r in reaction_analysis.values() for t in r.get("tuples", [])]
    if not all_tuples:
        return 2, "ABSA tuple 0건 — aspect 분해 결과 없음"
    channels = {t.get("channel") for t in all_tuples}
    if channels < {"youtube", "community"}:
        return 3, (f"7-tuple 확보, 단일 채널({'·'.join(sorted(c for c in channels if c))}) "
                   "— 2채널 교차 미충족")
    dated_ratio = sum(1 for t in all_tuples if t.get("posted_at")) / len(all_tuples)
    gaps = []
    if dated_ratio < 0.5:
        gaps.append(f"posted_at 보유 {dated_ratio:.0%} < 50% (시점 분리 뷰 불충분)")
    if not suggestions:
        gaps.append("suggestion 0건")
    if not gaps:
        return 5, "2채널 교차 + 가중치 + 시점 분리 뷰 + suggestion 분리 충족"
    return 4, f"2채널 교차 충족. 5점 미달 — {', '.join(gaps)}"


def _sanitize_insights(llm_out: dict, matrix: dict) -> dict:
    """LLM 서술의 환각 aspect 제거."""
    llm_out["aspect_insights"] = [
        i for i in llm_out.get("aspect_insights", []) if i.get("aspect") in matrix
    ]
    return llm_out


def _make_analyzer(system_prompt: str):
    from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer  # 지연 import
    return ClaudeCodeCliAnalyzer(system_prompt=system_prompt, timeout=_LLM_TIMEOUT_SEC)


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def reaction_insight_node(
    state: DomainAnalysisState, config: dict | None = None, analyzer=None
) -> dict:
    """ABSA 집계 → reaction_insight envelope (코드 집계 + LLM 서술)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    reaction_analysis: dict = state.get("reaction_analysis") or {}
    if not reaction_analysis:
        return make_error_result(
            REPORT_TYPE, started_at,
            "reaction_analysis 가 비어 있습니다 — ABSA 노드 산출 확인 필요.")

    entry = get_report_entry(state, REPORT_TYPE) or {}
    aspect_labels = {
        a.get("aspect_id"): a.get("label", a.get("aspect_id"))
        for a in entry.get("aspect_codebook") or [] if isinstance(a, dict)
    }
    own_id = (state.get("own_product") or {}).get("product_id", "")

    # ── 코드 파트: 집계 (결정론) ────────────────────────────────────────────
    matrix      = build_aspect_matrix(reaction_analysis)
    top_quotes  = select_top_quotes(reaction_analysis)
    suggestions = build_suggestions(reaction_analysis)
    timeline    = build_timeline(reaction_analysis)
    score, score_rationale = compute_rubric(reaction_analysis, suggestions)
    channel_meta = {
        cid: {k: r.get(k) for k in ("channel_counts", "sample_size", "collected_at")}
        for cid, r in reaction_analysis.items()
    }
    source_refs = [
        {"url": q["source_url"], "aspect": q["aspect"], "candidate_id": q["candidate_id"]}
        for q in top_quotes if q.get("source_url")
    ]

    # ── LLM 파트: 서술 (캐시 + degrade) ─────────────────────────────────────
    system_prompt = _load_text(AGENTS_DIR / REPORT_TYPE / "system_prompt_kr.md")
    output_schema = _load_json(AGENTS_DIR / REPORT_TYPE / "output.schema.json")
    if system_prompt is None or output_schema is None:
        return make_error_result(
            REPORT_TYPE, started_at, f"agents/{REPORT_TYPE}/ prompt·schema 로드 실패.")

    if analyzer is None:
        analyzer = _make_analyzer(system_prompt)
    context = make_cache_context(
        agent_id=REPORT_TYPE, model=getattr(analyzer, "model", "claude_cli"),
        system_prompt=system_prompt, output_schema=output_schema)
    payload = {
        "own_candidate_id": own_id,
        "channel_weights":  CHANNEL_WEIGHTS,
        "aspect_labels":    aspect_labels,
        "aspect_matrix":    matrix,
        "top_quotes":       top_quotes,
        "suggestions":      suggestions,
    }
    cache_input = {
        "report_type": REPORT_TYPE,
        "payload_sha256": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    }

    degraded_error = ""
    llm_out = load_agent_output(
        agent_id=REPORT_TYPE, cache_input=cache_input, context=context,
        output_schema=output_schema, logger=logger)
    if llm_out is None:
        prompt = ("다음 집계된 사용자 반응 데이터에 대한 인사이트를 서술하라 "
                  "(수치 재계산 금지).\n\n```json\n"
                  + json.dumps(payload, ensure_ascii=False) + "\n```")
        try:
            llm_out = analyzer.call_with_schema(prompt, output_schema)
            store_agent_output(agent_id=REPORT_TYPE, cache_input=cache_input,
                               context=context, output=llm_out, logger=logger)
        except Exception as exc:  # noqa: BLE001 — CM-D5 degrade
            degraded_error = (f"LLM 서술 실패 — 집계 전용 degrade: "
                              f"{type(exc).__name__}: {str(exc)[:200]}")
            logger.error("reaction_insight_node: %s", degraded_error)
            llm_out = None

    if llm_out is not None:
        llm_out = _sanitize_insights(llm_out, matrix)
        aspect_insights = llm_out["aspect_insights"]
        overall_summary = llm_out["overall_summary"]
        warnings = list(llm_out.get("warnings", []))
    else:
        aspect_insights, overall_summary = [], "(degraded — LLM 서술 생략, 집계만 제공)"
        warnings = [degraded_error]
    warnings.append(f"score_rationale: {score_rationale}")

    content = {
        "title":           entry.get("label") or "고객 반응 인사이트",
        "aspect_labels":   aspect_labels,
        "aspect_matrix":   matrix,
        "top_quotes":      top_quotes,
        "suggestions":     suggestions,
        "timeline_view":   timeline,
        "channel_meta":    channel_meta,        # AP-3 — 표본 크기·수집 시점
        "channel_weights": CHANNEL_WEIGHTS,     # RI-D7
        "aspect_insights": aspect_insights,
        "overall_summary": overall_summary,
    }
    envelope = build_report_envelope(
        report_type=REPORT_TYPE,
        rubric_version=RUBRIC_VERSION,
        categories=entry.get("categories") or [],
        content=content,
        evaluation_score=score,
        source_references=source_refs,
        warnings=warnings,
    )

    out: dict = {
        "report_outputs": {REPORT_TYPE: envelope},   # merge reducer — 자기 키만
        "agent_steps":    [make_completed_step(REPORT_TYPE, started_at)],
    }
    if degraded_error:
        out["errors"] = [{"node": f"{REPORT_TYPE}_node", "error": degraded_error,
                          "timestamp": datetime.now(timezone.utc).isoformat()}]
    return out
