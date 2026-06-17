"""
server/graph/official_source_store.py
--------------------------------------
상품별 검증된 공식 URL의 영구 캐시.

설계 목적
---------
한 번 검증된 자사·경쟁사·기능 대안의 공식 URL을 candidate_id 단위로
JSON 파일에 저장하고, 후속 분석에서 동일 상품에 대해 Brave 탐색과 LLM 검증을
완전히 건너뛸 수 있도록 한다.

캐시 hit 시에도 primary_url을 HTTP로 1회 재검증해 죽은 링크 자동 폐기
→ 신선도(freshness)와 속도(speed)의 균형을 보장한다.

파일 위치 / 형식
----------------
data/cache/official_sources.json

{
  "_meta": {
    "schema_version": 1,
    "created_at": "...",
    "updated_at": "...",
    "total_entries": N
  },
  "entries": {
    "own_tossbnk": {
      "candidate_id": "own_tossbnk",
      "source_type": "official",
      "brand": "...",
      "product_name": "...",
      "primary_url": "...",                 // 대표(primary) 공식 URL
      "official_urls": ["...", "..."],      // 복수 공식 도메인 허용 목록(검증된 공식 URL 전부, primary 포함).
                                            //   부재(구 스키마) 시 소비측은 [primary_url] 로 폴백.
                                            //   resolver 가 이 필드 없는 official 엔트리는 캐시 미스로 재해석.
      "http_status": 200,
      "validated": true,
      "llm_selected": true,
      "llm_confidence": 0.95,
      "fallback_urls": [...],
      "_cached_at":    "ISO 8601",  // 최초 캐시 저장 시각
      "_validated_at": "ISO 8601"   // 마지막 검증 성공 시각
    },
    "func_local_atm": {
      "candidate_id": "func_local_atm",
      "source_type": "reference",
      "method_name": "...",
      "provider_type": "...",
      "reference_sources": [...],
      "validated": true,
      "_cached_at": "...",
      "_validated_at": "..."
    }
  }
}

캐시 키 설계
------------
candidate_id 자체(예: comp_kakaobank)를 키로 사용한다.
slug.py의 deterministic_normalize 결과이므로 동일 상품의 표기 차이는
이미 슬러그 단계에서 흡수된다.

스레드 안전성
-------------
파일 읽기·쓰기는 threading.Lock으로 보호된다.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


class OfficialSourceStore:
    """상품별 검증된 공식 URL을 JSON에 영구 저장하는 단일 파일 캐시."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load()

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def get(self, candidate_id: str, ttl_days: int) -> dict | None:
        """
        candidate_id에 해당하는 캐시 항목을 반환한다.

        Parameters
        ----------
        candidate_id : str
            own_*, comp_*, func_* 슬러그.
        ttl_days : int
            마지막 검증 시각으로부터 이 일수 이내의 항목만 유효 처리.
            0 또는 음수면 무한 유효.

        Returns
        -------
        dict | None
            유효 항목 dict(deep copy) 또는 None.
            반환값에는 `_cached_at`, `_validated_at` 메타도 포함된다(caller가 무시 가능).
        """
        with self._lock:
            entry = self._data["entries"].get(candidate_id)
            if not entry:
                return None
            if ttl_days > 0 and self._is_expired(entry.get("_validated_at"), ttl_days):
                logger.info(
                    "OfficialSourceStore.get[%s]: TTL 만료 — 캐시 미스 처리",
                    candidate_id,
                )
                return None
            return copy.deepcopy(entry)

    def set(self, candidate_id: str, source: dict) -> None:
        """
        검증된 source dict를 캐시에 저장한다.

        source는 official_source_resolver_node가 만든 official 또는 reference dict이며
        반드시 source["validated"] == True 여야 한다. (caller 책임)
        """
        now = _now_iso()
        with self._lock:
            existing = self._data["entries"].get(candidate_id, {})
            stored = {
                **copy.deepcopy(source),
                "_cached_at":    existing.get("_cached_at", now),
                "_validated_at": now,
            }
            self._data["entries"][candidate_id] = stored
            self._data["_meta"]["updated_at"]    = now
            self._data["_meta"]["total_entries"] = len(self._data["entries"])
            self._save()

    def invalidate(self, candidate_id: str) -> None:
        """캐시 항목 1개 제거 (HTTP 재검증 실패 시 호출)."""
        with self._lock:
            removed = self._data["entries"].pop(candidate_id, None)
            if removed is not None:
                now = _now_iso()
                self._data["_meta"]["updated_at"]    = now
                self._data["_meta"]["total_entries"] = len(self._data["entries"])
                self._save()
                logger.info("OfficialSourceStore.invalidate[%s]: 항목 제거", candidate_id)

    def stats(self) -> dict:
        """캐시 현황을 반환한다(디버깅·관리용)."""
        with self._lock:
            return dict(self._data["_meta"])

    # ── 내부 메서드 ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                    data.setdefault("_meta", {})
                    return data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "OfficialSourceStore: 캐시 파일 손상 — 빈 캐시로 시작 (%s)", exc,
                )
        return self._empty_structure()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _empty_structure() -> dict:
        now = _now_iso()
        return {
            "_meta": {
                "schema_version": _SCHEMA_VERSION,
                "created_at":     now,
                "updated_at":     now,
                "total_entries":  0,
                "description":    "상품별 검증된 공식 URL의 영구 캐시 — candidate_id 키.",
            },
            "entries": {},
        }

    @staticmethod
    def _is_expired(validated_at: Any, ttl_days: int) -> bool:
        if not isinstance(validated_at, str):
            return True
        try:
            ts = datetime.fromisoformat(validated_at)
        except ValueError:
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - ts > timedelta(days=ttl_days)


# ── 모듈 레벨 싱글톤 ────────────────────────────────────────────────────────────
# 노드에서 import 후 즉시 사용 가능. 경로는 config에서 lazy 로드.

_store: OfficialSourceStore | None = None
_init_lock = threading.Lock()


def get_store() -> OfficialSourceStore:
    """모듈 레벨 싱글톤 OfficialSourceStore를 반환한다."""
    global _store
    if _store is None:
        with _init_lock:
            if _store is None:
                from server.config import OFFICIAL_SOURCE_STORE_PATH
                _store = OfficialSourceStore(OFFICIAL_SOURCE_STORE_PATH)
    return _store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
