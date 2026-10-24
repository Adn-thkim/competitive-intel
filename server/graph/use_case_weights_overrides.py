"""
server/graph/use_case_weights_overrides.py
------------------------------------------
use_case_weights 수동 주입(override) — **소비자 세그먼트 PoC** 용.

action_lens·taxonomy·domain_modeling 을 건드리지 않고(현재 캐시 히트 경로 유지),
comparison_matrix 의 use_case_weights 를 own_id 단위로 교체한다. PoC 스크립트
(scripts/poc_consumer_segments.py)가 도메인별 소비자 세그먼트를 생성해 이 파일에 적재하고,
comparison_matrix_node 가 LLM 산출 후 이 값으로 덮어쓴다(LLM 캐시 불변).

저장 형식: data/overrides/use_case_weights_overrides.json
  { "overrides": { "<own_id>": [ {"use_case": str, "weights": {feat_*: 0~1}, "description"?: str} ] } }
"""
from __future__ import annotations

import json
import logging

from server.config import BASE_DIR

logger = logging.getLogger(__name__)

OVERRIDES_PATH = BASE_DIR / "data" / "overrides" / "use_case_weights_overrides.json"


def get_override_weights(own_id: str) -> list | None:
    """own_id 의 세그먼트 use_case_weights 리스트. 없으면 None."""
    if not own_id or not OVERRIDES_PATH.exists():
        return None
    try:
        data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("use_case_weights_overrides 로드 실패: %s", exc)
        return None
    raw = (data.get("overrides") or {}).get(own_id)
    if not isinstance(raw, list):
        return None
    out: list[dict] = []
    for seg in raw:
        if isinstance(seg, dict) and seg.get("use_case") and isinstance(seg.get("weights"), dict):
            entry = {"use_case": seg["use_case"], "weights": seg["weights"]}
            if seg.get("description"):
                entry["description"] = seg["description"]
            if seg.get("summary"):           # 세그먼트 한 줄(verbatim) — narrate LLM 대체
                entry["summary"] = seg["summary"]
            if isinstance(seg.get("zones"), dict):   # 세그먼트별 zone(win|bat|lose) — 칩 색상
                entry["zones"] = seg["zones"]
            out.append(entry)
    return out or None


def get_override_overall_comment(own_id: str) -> str | None:
    """한눈 요약 전체 코멘트(zone_summary.overall_comment) verbatim override. 없으면 None."""
    if not own_id or not OVERRIDES_PATH.exists():
        return None
    try:
        data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("use_case_weights_overrides 로드 실패: %s", exc)
        return None
    txt = (data.get("overall_comments") or {}).get(own_id)
    return txt if isinstance(txt, str) and txt.strip() else None
