"""
server/graph/feature_pool_overrides.py
--------------------------------------
feature_pool 셀 수동 보정(override) 레이어.

추출(official_content_collection)이 못 잡거나, 출처가 앱·제3자 도메인에 있어
파이프라인으로 회수 불가한 (candidate_id, feature_id) 값을 **영속 보정**한다.
`assemble_feature_pool` 직후 적용되므로, 추출 캐시·프롬프트·페이지·feature 변동과
무관하게 항상 유지된다(캐시 직접 주입이 재실행마다 날아가던 문제의 근본 해결).

저장 형식: data/overrides/feature_pool_overrides.json
  {
    "overrides": {
      "<candidate_id>::<feature_id>": {
        "value": str, "evidence": str, "source_url": str,
        "extraction_status": "explicit", "confidence": 1.0, ...
      }
    }
  }
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from server.config import BASE_DIR

logger = logging.getLogger(__name__)

OVERRIDES_PATH = BASE_DIR / "data" / "overrides" / "feature_pool_overrides.json"

# 셀 표준 키 기본값 — override 가 일부만 지정해도 온전한 셀이 되도록 채운다.
_CELL_DEFAULTS = {
    "value": "", "value_numeric": None, "unit": "", "as_of": "",
    "extraction_status": "explicit", "evidence": "", "source_url": "",
    "source_origin": "manual_override", "confidence": 1.0,
    "is_promotional": False, "valid_until": "",
    # 표시 제어(결정론) — build_feature_table 가 override 셀에서 직접 사용.
    # headline=표에 노출할 짧은 핵심값, condition=조건(후버·조건 아이콘), items=나열형.
    "headline": "", "condition": "", "items": [],
}


def load_overrides() -> dict:
    """overrides 맵 {'<cid>::<fid>': cell} 을 로드. 없거나 손상 시 빈 dict."""
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("feature_pool_overrides 로드 실패: %s", exc)
        return {}
    ov = data.get("overrides")
    return ov if isinstance(ov, dict) else {}


def apply_overrides(feature_pool: dict, logger_: logging.Logger | None = None) -> int:
    """feature_pool[feature_id][candidate_id] 에 override 셀을 덮어쓴다 (in-place).

    반환: 적용 건수. 추출 결과 유무와 무관하게 항상 덮어쓴다.
    """
    lg = logger_ or logger
    overrides = load_overrides()
    if not overrides:
        return 0
    applied = 0
    for key, cell in overrides.items():
        if "::" not in key or not isinstance(cell, dict):
            continue
        cid, fid = key.split("::", 1)
        merged = {**_CELL_DEFAULTS, **cell, "_override": True}
        feature_pool.setdefault(fid, {})[cid] = merged
        applied += 1
        lg.info("feature_pool override 적용: %s × %s", cid, fid)
    return applied
