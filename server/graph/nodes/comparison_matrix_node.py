"""
server/graph/nodes/comparison_matrix_node.py
--------------------------------------------
`comparison_matrix` 리포트 노드 — feature_pool 소비 → 비교 매트릭스 envelope 산출.
설계: docs/design/comparison_matrix_node_design.md (CM-D1~CM-D5)

책임 분리 (CM-D1)
-----------------
- 코드 (결정론): feature_table 구성 · 셀 표기 규칙(CM-D2) · promotional/traps
  footnote · source_references 집계 · LLM 출력 ID 가드 · degrade 점수 산정.
- LLM (ClaudeCodeCliAnalyzer, 리포트당 1회): zone_summary(Winning/Battling/Losing) ·
  harvey_balls(정성 5단계) · use_case_weights(action_lens 시) · evaluation_score.

흐름 분류 (§11-10)
------------------
- 흐름 A leaf — official_content_collection 완료 직후 실행.
- zone_summary 는 battlecard(흐름 B) 인용 대상.

read keys
---------
- feature_pool / product_profiles  (official_content_collection 산출)
- domain_taxonomy.report_config["comparison_matrix"]  (active·label·categories·
  feature_labels·action_lens)
- selected_feature_ids / selected_competitor_ids / own_product

write keys
----------
- report_outputs["comparison_matrix"]  (build_report_envelope 표준 envelope)

LLM 실패 시 (CM-D5)
-------------------
fail 시키지 않고 표 전용 envelope 으로 degrade — zone/harvey 빈 값 + 결정론 점수
(수치+단위+출처 충족 시 3, 미충족 2) + warnings 기록. comparison_matrix 완전 실패는
흐름 B(positioning_map·battlecard)를 차단하므로 표만이라도 전달한다 (§7 사상).
"""

from __future__ import annotations

import hashlib
import json
import logging
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

REPORT_TYPE    = "comparison_matrix"
RUBRIC_VERSION = "report_taxonomy.md §2-1 (2026-05)"

_LLM_TIMEOUT_SEC = 300

# CM-D2 — AP-2 자동 경고 대상 절대 단어
_ABSOLUTE_WORDS = ("무료", "0%", "면제", "없음")


# ─── 코드 파트: feature_table 구성 (CM-D1·CM-D2) ─────────────────────────────

def _candidate_rows_order(feature_pool: dict, own_id: str) -> list[str]:
    """own 첫 행 + comp_*/func_* 정렬 (결정론)."""
    cids = sorted({cid for cells in feature_pool.values() for cid in cells})
    return ([own_id] if own_id in cids else []) + [c for c in cids if c != own_id]


def _feature_columns(entry: dict, feature_pool: dict,
                     selected_feature_ids: list[str]) -> list[dict]:
    """선택 feature 중 feature_pool 에 존재하는 열 (taxonomy feature_labels 라벨)."""
    labels = entry.get("feature_labels") or {}
    categories = entry.get("categories") or []
    cols = []
    for fid in selected_feature_ids:
        if fid not in feature_pool:
            continue
        cols.append({
            "feature_id": fid,
            "label":      labels.get(fid, fid),
            "category":   next((c for c in categories if c.lower() in fid.lower()), ""),
        })
    return cols


def build_feature_table(
    feature_pool: dict, entry: dict, selected_feature_ids: list[str],
    own_id: str, name_by_cid: dict[str, str],
) -> tuple[dict, list[dict], list[str]]:
    """결정론적 표 구성 + 표기 규칙(CM-D2) + footnote/자동 경고.

    반환: (feature_table, promotional_footnotes, traps_footnote)
    """
    columns = _feature_columns(entry, feature_pool, selected_feature_ids)
    promotional_footnotes: list[dict] = []
    traps: list[str] = []
    rows: list[dict] = []

    for cid in _candidate_rows_order(feature_pool, own_id):
        cells: dict[str, dict] = {}
        for col in columns:
            fid = col["feature_id"]
            src = feature_pool.get(fid, {}).get(cid)
            if src is None:
                src = {"value": "", "value_numeric": None, "unit": "", "as_of": "",
                       "extraction_status": "not_found", "source_url": "",
                       "confidence": 0, "is_promotional": False, "valid_until": ""}

            status = src.get("extraction_status", "not_found")
            manual = status == "requires_manual_check"
            footnote_refs: list[int] = []

            # CM-D2 표기 규칙
            if status in ("not_found", "unknown"):
                display = "미확인"
            else:
                display = src.get("value", "")
                if manual:
                    display = f"{display} (수동 검토 필요)"
                if src.get("is_promotional"):
                    ref = len(promotional_footnotes) + 1
                    until = src.get("valid_until", "")
                    promotional_footnotes.append({
                        "ref": ref, "candidate_id": cid, "feature_id": fid,
                        "valid_until": until,
                        "note": f"기간 한정 혜택{f' (~{until})' if until else ''} — "
                                "영구 조건과 구분 필요 (AP-1)",
                    })
                    display = f"{display} [기간한정{f' ~{until}' if until else ''}]"
                    footnote_refs.append(ref)

            # AP 자동 경고 (코드 파트)
            value = src.get("value", "")
            if status == "partial" and any(w in value for w in _ABSOLUTE_WORDS):
                traps.append(
                    f"AP-2 후보: {cid} × {fid} — 절대 단어 포함 값이 partial 상태 "
                    "(범위·조건 미확인 가능성)")
            if src.get("value_numeric") is not None and not src.get("as_of"):
                traps.append(f"AP-3 후보: {cid} × {fid} — 정량 값에 기준 시점(as_of) 미표기")

            cells[fid] = {
                "display":               display,
                "value":                 value,
                "value_numeric":         src.get("value_numeric"),
                "unit":                  src.get("unit", ""),
                "as_of":                 src.get("as_of", ""),
                "extraction_status":     status,
                "confidence":            src.get("confidence", 0),
                "source_url":            src.get("source_url", ""),
                "is_promotional":        bool(src.get("is_promotional")),
                "valid_until":           src.get("valid_until", ""),
                "manual_check_required": manual,
                "footnote_refs":         footnote_refs,
            }
        rows.append({
            "candidate_id":   cid,
            "candidate_name": name_by_cid.get(cid, cid),
            "is_own":         cid == own_id,
            "cells":          cells,
        })

    return {"columns": columns, "rows": rows}, promotional_footnotes, sorted(set(traps))


def _collect_source_references(feature_table: dict) -> list[dict]:
    """envelope source_references — 셀 출처 URL × feature_id 집계 (결정론)."""
    refs: dict[tuple[str, str], dict] = {}
    for row in feature_table["rows"]:
        for fid, cell in row["cells"].items():
            url = cell.get("source_url", "")
            if url:
                refs.setdefault((url, fid), {
                    "url": url, "feature_id": fid,
                    "candidate_id": row["candidate_id"],
                })
    return [refs[k] for k in sorted(refs)]


def _deterministic_score(feature_table: dict) -> int:
    """CM-D5 degrade 점수 — Rubric 결정론 부분: 수치+단위+출처 충족 시 3, 아니면 2."""
    quantitative = [
        cell for row in feature_table["rows"] for cell in row["cells"].values()
        if cell["value_numeric"] is not None
    ]
    if quantitative and all(c["unit"] and c["source_url"] for c in quantitative):
        return 3
    return 2


# ─── LLM 파트 (CM-D1 — 판정·점수만) ──────────────────────────────────────────

def _sanitize_llm_output(llm_out: dict, feature_table: dict) -> dict:
    """LLM 환각 ID 가드 — columns·rows 에 없는 feature_id/candidate_id 제거."""
    valid_fids = {c["feature_id"] for c in feature_table["columns"]}
    valid_cids = {r["candidate_id"] for r in feature_table["rows"]}

    zone = llm_out.get("zone_summary") or {}
    for key in ("winning", "battling", "losing"):
        zone[key] = [z for z in zone.get(key, []) if z.get("feature_id") in valid_fids]

    harvey = []
    for h in llm_out.get("harvey_balls", []):
        if h.get("feature_id") not in valid_fids:
            continue
        h["ratings"] = {c: v for c, v in (h.get("ratings") or {}).items()
                        if c in valid_cids}
        harvey.append(h)
    llm_out["harvey_balls"] = harvey
    return llm_out


def _make_analyzer(system_prompt: str):
    """기본 LLM 어댑터 (CM-D4) — 테스트는 run 파라미터로 fake 주입."""
    from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer  # 지연 import
    return ClaudeCodeCliAnalyzer(system_prompt=system_prompt, timeout=_LLM_TIMEOUT_SEC)


def _llm_cache_input(feature_table: dict, own_id: str) -> dict:
    table_hash = hashlib.sha256(
        json.dumps(feature_table, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {"report_type": REPORT_TYPE, "own_candidate_id": own_id,
            "feature_table_sha256": table_hash}


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def comparison_matrix_node(
    state: DomainAnalysisState, config: dict | None = None, analyzer=None
) -> dict:
    """CM-D1 분리형 comparison_matrix 리포트 생성."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    feature_pool: dict = state.get("feature_pool") or {}
    if not feature_pool:
        return make_error_result(
            REPORT_TYPE, started_at,
            "feature_pool 이 비어 있습니다 — official_content_collection 산출 확인 필요.")

    entry = get_report_entry(state, REPORT_TYPE) or {}
    own_id = (state.get("own_product") or {}).get("product_id", "")
    selected_feature_ids = state.get("selected_feature_ids") or sorted(feature_pool)
    name_by_cid = {
        p.get("candidate_id", ""): p.get("product_name", "")
        for p in state.get("product_profiles") or []
    }

    # ── 코드 파트: 표 구성 (결정론) ─────────────────────────────────────────
    feature_table, promo_footnotes, traps = build_feature_table(
        feature_pool, entry, selected_feature_ids, own_id, name_by_cid)
    if not feature_table["columns"]:
        return make_error_result(
            REPORT_TYPE, started_at,
            "feature_table 열 0건 — selected_feature_ids 와 feature_pool 불일치.")
    source_refs = _collect_source_references(feature_table)

    # ── LLM 파트: 판정·점수 (캐시 + degrade) ───────────────────────────────
    system_prompt = _load_text(AGENTS_DIR / REPORT_TYPE / "system_prompt_kr.md")
    output_schema = _load_json(AGENTS_DIR / REPORT_TYPE / "output.schema.json")
    if system_prompt is None or output_schema is None:
        return make_error_result(
            REPORT_TYPE, started_at, f"agents/{REPORT_TYPE}/ prompt·schema 로드 실패.")

    if analyzer is None:
        analyzer = _make_analyzer(system_prompt)
    context = make_cache_context(
        agent_id=REPORT_TYPE,
        model=getattr(analyzer, "model", "claude_cli"),
        system_prompt=system_prompt,
        output_schema=output_schema,
    )
    cache_input = _llm_cache_input(feature_table, own_id)

    llm_out: dict | None = load_agent_output(
        agent_id=REPORT_TYPE, cache_input=cache_input, context=context,
        output_schema=output_schema, logger=logger)
    degraded_error = ""
    if llm_out is None:
        payload = {
            "own_candidate_id": own_id,
            "categories":       entry.get("categories") or [],
            "action_lens":      entry.get("action_lens") or None,
            "feature_table":    feature_table,
        }
        prompt = (
            "다음 비교 매트릭스 표 데이터를 판정하라 (표 재생성 금지).\n\n```json\n"
            + json.dumps(payload, ensure_ascii=False)
            + "\n```"
        )
        try:
            llm_out = analyzer.call_with_schema(prompt, output_schema)
            store_agent_output(
                agent_id=REPORT_TYPE, cache_input=cache_input,
                context=context, output=llm_out, logger=logger)
        except Exception as exc:  # noqa: BLE001 — CM-D5 degrade
            degraded_error = f"LLM 판정 실패 — 표 전용 degrade: {type(exc).__name__}: {str(exc)[:200]}"
            logger.error("comparison_matrix_node: %s", degraded_error)
            llm_out = None

    if llm_out is not None:
        llm_out = _sanitize_llm_output(llm_out, feature_table)
        zone = llm_out["zone_summary"]
        harvey = llm_out["harvey_balls"]
        use_case_weights = llm_out.get("use_case_weights", [])
        score = llm_out["evaluation_score"]
        warnings = list(llm_out.get("warnings", []))
        if llm_out.get("score_rationale"):
            warnings.append(f"score_rationale: {llm_out['score_rationale']}")
    else:
        zone = {"winning": [], "battling": [], "losing": [],
                "overall_comment": "(degraded — LLM 판정 생략, 표 데이터만 제공)"}
        harvey, use_case_weights = [], []
        score = _deterministic_score(feature_table)
        warnings = [degraded_error]

    # ── envelope 조립 ───────────────────────────────────────────────────────
    content = {
        "title":                 entry.get("label") or "비교 매트릭스",
        "feature_table":         feature_table,
        "zone_summary":          zone,
        "harvey_balls":          harvey,
        "use_case_weights":      use_case_weights,
        "promotional_footnotes": promo_footnotes,
        "traps_footnote":        traps,
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
        # CM-D3: 현 시리즈는 단일 리포트 노드 — 기본 replace 안전.
        # 복수 리포트 병렬화 전 merge reducer 도입 필요 (설계 문서 §6).
        "report_outputs": {**(state.get("report_outputs") or {}), REPORT_TYPE: envelope},
        "agent_steps":    [make_completed_step(REPORT_TYPE, started_at)],
    }
    if degraded_error:
        out["errors"] = [{
            "node":      f"{REPORT_TYPE}_node",
            "error":     degraded_error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    return out
