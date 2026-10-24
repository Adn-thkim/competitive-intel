"""
server/graph/nodes/comparison_matrix_node.py
--------------------------------------------
`comparison_matrix` 리포트 노드 — feature_pool 소비 → 비교 매트릭스 envelope 산출.
설계: docs/design/comparison_matrix_node_design.md (CM-D1~CM-D5)

책임 분리 (CM-D1)
-----------------
- 코드 (결정론): feature_table 구성 · 셀 표기 규칙(CM-D2) · promotional/traps
  footnote · source_references 집계 · LLM 출력 ID 가드 · degrade 점수 산정.
- LLM (ClaudeCodeCliAnalyzer, 리포트당 1회): product_summary(제품 관점 요약 — 상품별 설계
  요약 · 자사 특징 · 공통점/차이점 · 조건부 강점/약점) · evaluation_score.
  (v2.0: zone_summary·harvey_balls·use_case_weights 폐기. 활용유형 분류는 positioning_map 이관.
   구 narrate_use_cases·_zone_map 은 미사용 데드 함수로 남음 — 후속 정리 대상.)

흐름 분류 (§11-10)
------------------
- 흐름 A leaf — official_content_collection 완료 직후 실행.

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
                     selected_feature_ids: list[str],
                     label_map: dict[str, str] | None = None) -> list[dict]:
    """선택 feature 중 feature_pool 에 존재하는 열.

    라벨 우선순위: label_map(analysis_features 의 한국어 feature_name) >
    taxonomy feature_labels > feature_id. taxonomy 라벨이 영문/누락이어도 한국어
    feature_name 으로 표시되도록 한다.
    """
    label_map = label_map or {}
    labels = entry.get("feature_labels") or {}
    categories = entry.get("categories") or []
    cols = []
    for fid in selected_feature_ids:
        if fid not in feature_pool:
            continue
        cols.append({
            "feature_id": fid,
            "label":      label_map.get(fid) or labels.get(fid) or fid,
            "category":   next((c for c in categories if c.lower() in fid.lower()), ""),
        })
    return cols


def build_feature_table(
    feature_pool: dict, entry: dict, selected_feature_ids: list[str],
    own_id: str, name_by_cid: dict[str, str],
    label_map: dict[str, str] | None = None,
    norm_map: dict | None = None,
) -> tuple[dict, list[dict], list[str]]:
    """결정론적 표 구성 + 표기 규칙(CM-D2) + footnote/자동 경고.

    label_map: feature_id → 한국어 라벨(analysis_features feature_name). 없으면
    taxonomy feature_labels → feature_id 순으로 fallback.
    반환: (feature_table, promotional_footnotes, traps_footnote)
    """
    columns = _feature_columns(entry, feature_pool, selected_feature_ids, label_map)
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
            # 행 정규화 결과(headline/condition). norm_map 없으면(테스트·degrade) value 폴백.
            # 단, 수동 보정(_override) 셀은 정규화를 건너뛰고 value 를 그대로 노출한다
            # (정규화 LLM 이 보정값을 재가공·축약해 의도한 추가분을 떨어뜨리는 문제 방지).
            is_override = bool(src.get("_override"))
            if is_override:
                # 보정 셀: 정규화 LLM 대신 override 가 지정한 headline/condition/items 사용
                # (결정론). 지정 없으면 value 폴백. → 표엔 짧은 핵심값, 후버엔 조건·전체값.
                headline = (src.get("headline") or "").strip()
                condition = (src.get("condition") or "").strip()
                items = src.get("items") or []
            else:
                norm = (norm_map or {}).get((fid, cid)) or {}
                headline = (norm.get("headline") or "").strip()
                condition = (norm.get("condition") or "").strip()
                items = norm.get("items") or []   # 나열형 feature 대표 항목

            # CM-D2 표기 규칙
            if status in ("not_found", "unknown"):
                display = "미확인"
            elif is_override:
                display = " · ".join(items) if items else (headline or src.get("value", ""))
            else:
                # 나열형은 항목 join, 스칼라는 headline 우선(없으면 value 폴백)
                display = " · ".join(items) if items else (headline or src.get("value", ""))
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
                "headline":              headline,
                "condition":             condition,
                "items":                 items,
                "value":                 value,
                "value_numeric":         src.get("value_numeric"),
                "unit":                  src.get("unit", ""),
                "as_of":                 src.get("as_of", ""),
                "extraction_status":     status,
                "confidence":            src.get("confidence", 0),
                "evidence":              src.get("evidence", ""),
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


def _compute_rubric_score(
    feature_table: dict, product_summary: dict,
    promo_footnotes: list, traps: list,
) -> tuple[int, str]:
    """CM-D6 — 루브릭 점수 결정론 산정 (LLM 자기평가 폐기, 2026-06-06).

    배경: LLM 자기평가는 프롬프트 변화(예시 앵커링 등)에 따라 점수가 표류한다.
    Rubric §2-1 의 기준은 envelope 내용물로 기계 판정 가능하므로 코드가 채점한다.

    규칙 (누적):
      2점: 정량 셀 중 단위 또는 출처 누락 존재
      3점: 정량 셀 전부 수치+단위+출처 충족
      4점: 3점 + 제품 관점 요약 충실 (강점·약점 모두 비어있지 않음 — v2.0)
      5점: 4점 + 함정 각주(promotional·traps) 명시 + 정량 셀 as_of 전부 표기
    반환: (score, rationale)
    """
    quantitative = [
        cell for row in feature_table["rows"] for cell in row["cells"].values()
        if cell["value_numeric"] is not None
    ]
    met3 = bool(quantitative) and all(
        c["unit"] and c["source_url"] for c in quantitative)
    if not met3:
        return 2, "정량 값 중 단위 또는 출처 누락 — 3점 요건 미달"
    has_summary = bool((product_summary or {}).get("strengths")) and \
        bool((product_summary or {}).get("weaknesses"))
    if not has_summary:
        return 3, "수치·단위·출처 충족. 제품 요약(강점·약점) 부재로 4점 미달"

    missing_asof = sum(1 for c in quantitative if not c["as_of"])
    footnotes_present = bool(promo_footnotes or traps)
    if footnotes_present and missing_asof == 0:
        return 5, "제품 요약(강점·약점) 충실 + 함정 각주 명시 + 정량 셀 기준 시점 전부 표기"
    gaps = []
    if missing_asof:
        gaps.append(f"정량 셀 {missing_asof}건 기준 시점(as_of) 미표기")
    if not footnotes_present:
        gaps.append("함정 각주 부재")
    return 4, f"제품 요약 충실로 4점 충족. 5점 미달 — {', '.join(gaps)}"


# ─── LLM 파트 (CM-D1 — 판정·점수만) ──────────────────────────────────────────

def _empty_product_summary() -> dict:
    return {"per_product": [], "own_characteristics": [], "commonalities": [],
            "differences": [], "strengths": [], "weaknesses": []}


def _sanitize_llm_output(llm_out: dict, feature_table: dict) -> dict:
    """LLM 환각 ID 가드 — product_summary 의 candidate/feature ID 를 표 기준으로 정리."""
    valid_fids = {c["feature_id"] for c in feature_table["columns"]}
    valid_cids = {r["candidate_id"] for r in feature_table["rows"]}

    ps = llm_out.get("product_summary") or {}
    ps["per_product"] = [
        p for p in ps.get("per_product", []) if p.get("candidate_id") in valid_cids
    ]
    for key in ("strengths", "weaknesses"):
        for it in ps.get(key, []) or []:
            fid = it.get("feature_id")
            if fid and fid not in valid_fids:
                it.pop("feature_id", None)   # 환각 feature_id 만 제거(조건부 항목 자체는 유지)
    llm_out["product_summary"] = ps
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


# ─── 행 정규화 단계 (cross-candidate headline/condition) ──────────────────────
# comparison_matrix LLM(zone/harvey)과 분리된 별도 agent·캐시. feature 행 전체를
# 한 번에 보고 톤 통일(같은 사실→같은 headline) + 핵심/조건 분리. 실패 시 value 폴백.
NORMALIZER_AGENT_ID = "comparison_normalizer"


def _normalizer_input(feature_pool: dict, selected_feature_ids: list[str],
                      label_map: dict[str, str], domain_name: str = "") -> dict:
    feats = []
    for fid in selected_feature_ids:
        row = feature_pool.get(fid)
        if not row:
            continue
        cells = [{
            "candidate_id":      cid,
            "value":             src.get("value", ""),
            "extraction_status": src.get("extraction_status", "not_found"),
            "evidence":          (src.get("evidence", "") or "")[:300],
        } for cid, src in row.items()]
        feats.append({"feature_id": fid,
                      "label": label_map.get(fid, fid),
                      "cells": cells})
    # domain: 비교 축(comparison_spec)을 도메인 맥락에서 동적 추론하도록 정규화 LLM 에 제공.
    return {"domain": domain_name, "features": feats}


def normalize_feature_pool(
    feature_pool: dict, selected_feature_ids: list[str],
    label_map: dict[str, str], analyzer=None, logger=None, domain_name: str = "",
) -> dict:
    """행(feature) 단위 cross-candidate 정규화 → {(feature_id, candidate_id): {headline, condition}}.

    실패·로드불가 시 빈 dict 반환(호출부가 value 폴백). 자체 캐시(NORMALIZER_AGENT_ID).
    domain_name: 비교 축(comparison_spec) 동적 추론용 도메인 맥락(정규화 전용 반영).
    """
    lg = logger or logging.getLogger(__name__)
    norm_input = _normalizer_input(feature_pool, selected_feature_ids, label_map, domain_name)
    if not norm_input["features"]:
        return {}
    system_prompt = _load_text(AGENTS_DIR / NORMALIZER_AGENT_ID / "system_prompt_kr.md")
    output_schema = _load_json(AGENTS_DIR / NORMALIZER_AGENT_ID / "output.schema.json")
    if system_prompt is None or output_schema is None:
        lg.warning("comparison_normalizer: prompt·schema 로드 실패 — value 폴백")
        return {}
    if analyzer is None:
        analyzer = _make_analyzer(system_prompt)
    context = make_cache_context(
        agent_id=NORMALIZER_AGENT_ID,
        model=getattr(analyzer, "model", "claude_cli"),
        system_prompt=system_prompt, output_schema=output_schema)
    input_hash = hashlib.sha256(
        json.dumps(norm_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache_input = {"agent_id": NORMALIZER_AGENT_ID, "input_sha256": input_hash}

    out = load_agent_output(
        agent_id=NORMALIZER_AGENT_ID, cache_input=cache_input,
        context=context, output_schema=output_schema, logger=lg)
    if out is None:
        prompt = ("다음 비교표 값을 headline+condition 으로 정규화하라.\n\n```json\n"
                  + json.dumps(norm_input, ensure_ascii=False) + "\n```")
        try:
            out = analyzer.call_with_schema(prompt, output_schema)
            store_agent_output(
                agent_id=NORMALIZER_AGENT_ID, cache_input=cache_input,
                context=context, output=out, logger=lg)
        except Exception as exc:  # noqa: BLE001 — 정규화 실패는 비치명 (value 폴백)
            lg.warning("comparison_normalizer 실패 — value 폴백: %s", str(exc)[:200])
            return {}

    result: dict = {}
    for item in (out or {}).get("normalized", []):
        fid, cid = item.get("feature_id"), item.get("candidate_id")
        if fid and cid:
            result[(fid, cid)] = {
                "headline":  (item.get("headline") or "").strip(),
                "condition": (item.get("condition") or "").strip(),
                "items":     [s.strip() for s in (item.get("items") or []) if s and s.strip()],
            }
    return result


# ─── narrative 스텝 (세그먼트별 자사 관점 한 줄) ──────────────────────────────
NARRATOR_AGENT_ID = "comparison_narrator"


def _zone_by_fid(zone: dict) -> dict:
    """zone_summary → {feature_id: (zone_label, rationale)}."""
    m: dict = {}
    for code, key in (("우위", "winning"), ("접전", "battling"), ("열위", "losing")):
        for z in zone.get(key) or []:
            if z.get("feature_id"):
                m[z["feature_id"]] = (code, z.get("rationale", ""))
    return m


def narrate_use_cases(own_name, use_case_weights, zone, label_map, analyzer=None, logger=None):
    """use_case_weights 각 항목에 자사 관점 한 줄 `summary` 부착. 실패 시 원본 반환."""
    lg = logger or logging.getLogger(__name__)
    if not use_case_weights:
        return use_case_weights
    # override 가 이미 summary 를 제공한 세그먼트는 verbatim 유지 — 빈 것만 LLM 보조.
    missing = [u for u in use_case_weights if not (u.get("summary") or "").strip()]
    if not missing:
        return use_case_weights
    zmap = _zone_by_fid(zone or {})
    segs = []
    for u in missing:
        tops = sorted((u.get("weights") or {}).items(), key=lambda kv: -kv[1])[:3]
        feats = [{
            "label": label_map.get(fid, fid),
            "zone": zmap.get(fid, ("미확인", ""))[0],
            "rationale": (zmap.get(fid, ("", ""))[1] or "")[:160],
        } for fid, _w in tops]
        seg = {"use_case": u.get("use_case", ""), "features": feats}
        if u.get("description"):
            seg["description"] = u["description"]
        segs.append(seg)
    narr_input = {"own_name": own_name or "자사", "segments": segs}

    system_prompt = _load_text(AGENTS_DIR / NARRATOR_AGENT_ID / "system_prompt_kr.md")
    output_schema = _load_json(AGENTS_DIR / NARRATOR_AGENT_ID / "output.schema.json")
    if system_prompt is None or output_schema is None:
        lg.warning("comparison_narrator: prompt·schema 로드 실패 — summary 생략")
        return use_case_weights
    if analyzer is None:
        analyzer = _make_analyzer(system_prompt)
    context = make_cache_context(
        agent_id=NARRATOR_AGENT_ID, model=getattr(analyzer, "model", "claude_cli"),
        system_prompt=system_prompt, output_schema=output_schema)
    input_hash = hashlib.sha256(
        json.dumps(narr_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    cache_input = {"agent_id": NARRATOR_AGENT_ID, "input_sha256": input_hash}

    out = load_agent_output(
        agent_id=NARRATOR_AGENT_ID, cache_input=cache_input,
        context=context, output_schema=output_schema, logger=lg)
    if out is None:
        prompt = ("다음 세그먼트별 자사 관점 한 줄 요약을 작성하라.\n\n```json\n"
                  + json.dumps(narr_input, ensure_ascii=False) + "\n```")
        try:
            out = analyzer.call_with_schema(prompt, output_schema)
            store_agent_output(agent_id=NARRATOR_AGENT_ID, cache_input=cache_input,
                               context=context, output=out, logger=lg)
        except Exception as exc:  # noqa: BLE001 — narrative 실패는 비치명(요약 생략)
            lg.warning("comparison_narrator 실패 — summary 생략: %s", str(exc)[:200])
            return use_case_weights

    sm = {n.get("use_case"): n.get("summary", "") for n in (out or {}).get("narratives", [])}
    return [{**u, **({"summary": sm[u["use_case"]]} if sm.get(u.get("use_case")) else {})}
            for u in use_case_weights]


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def comparison_matrix_node(
    state: DomainAnalysisState, config: dict | None = None, analyzer=None,
    normalizer=None, narrator=None,
) -> dict:
    """CM-D1 분리형 comparison_matrix 리포트 생성."""
    started_at = datetime.now(timezone.utc).isoformat()

    if not is_report_active(state, REPORT_TYPE):
        return make_skip_result(REPORT_TYPE, started_at)

    # 사용자가 feature_selection 에서 선택한 리포트만 생성(reaction_insight·marketing_social 동일 게이트).
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
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
    # 한국어 feature 라벨 — analysis_features 의 feature_name 을 1차 소스로 사용
    # (taxonomy feature_labels 가 영문/누락이어도 한국어 표시 보장)
    label_map = {
        f.get("feature_id", ""): f.get("feature_name", "")
        for f in state.get("analysis_features") or []
        if f.get("feature_id") and f.get("feature_name")
    }

    # ── 행 정규화 (cross-candidate headline/condition) ──────────────────────
    # 정규화 분석기가 주입되면 그것으로, 아니면 비주입(production)일 때만 실제 호출.
    # analyzer 만 주입된 단위 테스트는 정규화를 건너뛰어(norm_map={}) value 폴백.
    if normalizer is not None or analyzer is None:
        norm_map = normalize_feature_pool(
            feature_pool, selected_feature_ids, label_map,
            analyzer=normalizer, logger=logger,
            domain_name=state.get("domain_name") or "")
    else:
        norm_map = {}

    # ── 코드 파트: 표 구성 (결정론) ─────────────────────────────────────────
    feature_table, promo_footnotes, traps = build_feature_table(
        feature_pool, entry, selected_feature_ids, own_id, name_by_cid, label_map,
        norm_map)
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
            "domain":           state.get("domain_name") or "",
            "own_candidate_id": own_id,
            "categories":       entry.get("categories") or [],
            "feature_table":    feature_table,
        }
        prompt = (
            "다음 비교 매트릭스 표 데이터로 제품 관점 요약(product_summary)을 산출하라 (표 재생성 금지).\n\n```json\n"
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
        product_summary = llm_out.get("product_summary") or _empty_product_summary()
        warnings = list(llm_out.get("warnings", []))
    else:
        product_summary = _empty_product_summary()
        warnings = [degraded_error]

    # CM-D6 — 점수는 LLM 자기평가가 아니라 코드가 결정론적으로 채점 (표류 방지)
    score, score_rationale = _compute_rubric_score(
        feature_table, product_summary, promo_footnotes, traps)
    warnings.append(f"score_rationale: {score_rationale}")

    # ── envelope 조립 ───────────────────────────────────────────────────────
    content = {
        "title":                 entry.get("label") or "비교 매트릭스",
        "feature_table":         feature_table,
        "product_summary":       product_summary,
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
        # CM-D3 (2026-06-06): merge_report_outputs reducer 도입 — 자기 키만 반환하면
        # LangGraph 가 병합한다. 기존 state 전개(spread)는 reducer 와 중복이라 제거.
        "report_outputs": {REPORT_TYPE: envelope},
        "agent_steps":    [make_completed_step(REPORT_TYPE, started_at)],
    }
    if degraded_error:
        out["errors"] = [{
            "node":      f"{REPORT_TYPE}_node",
            "error":     degraded_error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    return out
