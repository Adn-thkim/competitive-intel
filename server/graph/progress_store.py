"""
server/graph/progress_store.py
--------------------------------
LangGraph 파이프라인 노드의 실시간 진행 상태를 thread_id 별로 저장한다.

FastAPI /progress/{thread_id} 엔드포인트가 이 저장소를 읽어 프런트엔드에 반환하고,
프런트엔드는 1~2초 간격으로 폴링해 현재 단계를 UI에 표시한다.

단계(stage) 목록
----------------
  url_discovery        LLM이 candidate별 URL 후보를 탐색하는 단계
  url_validation       ThreadPoolExecutor로 URL을 병렬 HTTP 검증하는 단계
  url_retry_llm        url_retry_node의 자동 LLM 재탐색 단계 (interrupt 전)
  url_retry_validation 자동 재탐색 후 URL을 병렬 HTTP 검증하는 단계
  url_phase1_llm       Phase 1 사용자 재시도 — LLM/검색 재탐색 단계
  url_phase1_validation Phase 1 사용자 재시도 — URL 병렬 검증 단계

설계 원칙
---------
- 인메모리 저장 (서버 재시작 시 초기화, 영속성 불필요)
- threading.Lock으로 동시 쓰기 보호
- invoke 완료(또는 interrupt 발생) 후 api.py에서 clear_progress() 호출
"""

import threading
from datetime import datetime, timezone

_lock: threading.Lock = threading.Lock()
_store: dict[str, dict] = {}

# stage → 한국어 표시 메시지 매핑
STAGE_MESSAGES: dict[str, str] = {
    "url_discovery":        "URL 탐색 중",
    "url_validation":       "URL 검증 중",
    "url_retry_llm":        "실패 URL 재탐색 중",
    "url_retry_validation": "재탐색 URL 검증 중",
    "url_phase1_llm":       "URL 재탐색 중",
    "url_phase1_validation": "URL 재검증 중",
}


def set_progress(
    thread_id: str,
    stage: str,
    detail: str = "",
    current: int = 0,
    total: int = 0,
) -> None:
    """
    지정된 thread의 진행 상태를 갱신한다.

    Parameters
    ----------
    thread_id : str   LangGraph thread_id
    stage     : str   STAGE_MESSAGES 키 중 하나
    detail    : str   UI에 보조 텍스트로 표시할 추가 정보
    current   : int   현재 처리 완료된 항목 수 (0이면 미사용)
    total     : int   전체 항목 수 (0이면 미사용)
    """
    with _lock:
        _store[thread_id] = {
            "stage":      stage,
            "message":    STAGE_MESSAGES.get(stage, stage),
            "detail":     detail,
            "current":    current,
            "total":      total,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def get_progress(thread_id: str) -> dict | None:
    """thread_id의 현재 진행 상태를 반환한다. 없으면 None."""
    with _lock:
        return _store.get(thread_id)


def clear_progress(thread_id: str) -> None:
    """invoke 완료(또는 interrupt) 후 저장소에서 해당 thread 항목을 제거한다."""
    with _lock:
        _store.pop(thread_id, None)
