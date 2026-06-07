"""
server/graph/progress_store.py
--------------------------------
LangGraph 파이프라인 노드의 실시간 진행 상태를 thread_id 별로 저장한다.

FastAPI /progress/{thread_id} 엔드포인트가 이 저장소를 읽어 프런트엔드에 반환하고,
프런트엔드는 1~2초 간격으로 폴링해 현재 단계를 UI에 표시한다.

저장 구조 (per thread_id)
-------------------------
{
  "stage":      str,            # 단계 식별자 (STAGE_MESSAGES 키) — 분기 A의 단일 트랙
  "message":    str,            # 한국어 표시 메시지
  "detail":     str,            # 보조 텍스트
  "current":    int,            # 처리 완료 항목 수
  "total":      int,            # 전체 항목 수
  "updated_at": str,            # ISO 8601
  "candidates": list[dict],     # ← C-1: candidate별 진행 이벤트 누적 목록
  "branches":   dict[str, str], # ← v0.10.4 분기 B 트래킹 — {"domain_modeling": "pending|running|done|failed", ...}
}

branches (v0.10.4 신설)
-----------------------
v0.9 토폴로지에서 `competitor_discovery → {normalize_competitor_ids, domain_modeling}` fan-out
이후 두 분기가 동시에 흐르므로, stage 단일 값으로는 분기 B(domain_modeling)의 상태를
표현할 수 없다. `branches` dict로 분기별 독립 상태를 추적한다.

  키: 분기 식별자 ("domain_modeling")
  값: "pending" | "running" | "done" | "failed"

candidates 항목 구조
--------------------
{
  "candidate_id": str,
  "label":        str,          # UI 표시명 (brand + product_name 등)
  "stage":        str,          # "brave" | "fast_path" | "llm" | "http" | "done" | "failed"
  "status":       str,          # "pending" | "in_progress" | "done" | "failed"
  "primary_url":  str | None,   # optimistic UI용: LLM 결과 도착 시점 임시 노출
  "validated":    bool | None,  # HTTP 검증 완료 후에만 채워짐
  "elapsed_ms":   int | None,   # candidate 처리 wall-clock
  "updated_at":   str,
}

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
    "url_discovery":           "URL 탐색 중",
    "url_validation":          "URL 검증 중",
    "url_resolution_done":     "URL 탐색·검증 완료",
    "url_retry_llm":           "실패 URL 재탐색 중",
    "url_retry_validation":    "재탐색 URL 검증 중",
    "url_phase1_llm":          "URL 재탐색 중",
    "url_phase1_validation":   "URL 재검증 중",
    "url_retry_done":          "URL 재시도 단계 완료",
    # v0.10.9 — feature_url_mapper 4단계 노드 분리에 따른 stage 세분화
    "feature_mapping_brave":   "Brave URL 검색 중",
    "feature_mapping_meta":    "페이지 메타 수집 중",
    "feature_mapping_llm":     "AI 분석 항목 매핑 중",
    "feature_mapping_validate": "추가 URL 검증 중",
    # 기존 호환 — 단일 feature_mapping 단계 (UI STAGE_INDEX 매핑용)
    "feature_mapping":         "분석 항목 매핑 중",
    "feature_mapping_done":    "분석 항목 매핑 완료",
    # v0.13 — reaction_insight 시리즈 (수집·ABSA 단계)
    "reaction_collection":     "사용자 반응 수집 중",
    "reaction_analysis":       "반응 ABSA 분석 중",
}

# candidate.stage → UI 라벨 (C-1)
CANDIDATE_STAGE_LABELS: dict[str, str] = {
    "pending":   "대기 중",
    "brave":     "검색 중",
    "fast_path": "즉시 인정",
    "cached":    "이전 검증 결과 사용",
    "llm":       "검증 중",
    "http":      "도달성 확인 중",
    "done":      "완료",
    "failed":    "실패",
}


def set_progress(
    thread_id: str,
    stage: str,
    detail: str = "",
    current: int = 0,
    total: int = 0,
) -> None:
    """
    지정된 thread의 단계 진행 상태를 갱신한다.

    Parameters
    ----------
    thread_id : str   LangGraph thread_id
    stage     : str   STAGE_MESSAGES 키 중 하나
    detail    : str   UI에 보조 텍스트로 표시할 추가 정보
    current   : int   현재 처리 완료된 항목 수 (0이면 미사용)
    total     : int   전체 항목 수 (0이면 미사용)

    참고: candidates 목록 + branches dict는 보존되며, stage 전환 시에도 누적된 정보가 유지된다.
    """
    with _lock:
        prev = _store.get(thread_id) or {}
        prev_cands: list[dict] = prev.get("candidates", [])
        prev_branches: dict[str, str] = prev.get("branches", {})

        # current/total이 0(미지정)이면 누적된 candidate 정보로 자동 보정한다.
        # → update_candidate로 누적된 진행 카운트가 set_progress 호출로 사라지지 않도록.
        derived_current = sum(
            1 for c in prev_cands if c.get("status") in ("done", "failed")
        )
        derived_total = len(prev_cands)

        _store[thread_id] = {
            "stage":      stage,
            "message":    STAGE_MESSAGES.get(stage, stage),
            "detail":     detail,
            "current":    current if current > 0 else derived_current,
            "total":      total   if total   > 0 else derived_total,
            "updated_at": _now_iso(),
            "candidates": prev_cands,
            "branches":   prev_branches,
        }


def set_branch_status(thread_id: str, branch: str, status: str) -> None:
    """
    v0.9 토폴로지의 병렬 분기 상태를 추적한다 (v0.10.4 신설).

    stage 트랙(분기 A)과 별개로 분기 B의 노드 진행을 표현하기 위해 사용한다.
    예: domain_modeling 노드가 진입 시 "running", 완료 시 "done"을 emit.

    Parameters
    ----------
    thread_id : str   LangGraph thread_id
    branch    : str   분기 식별자 (예: "domain_modeling")
    status    : str   "pending" | "running" | "done" | "failed"
    """
    now = _now_iso()
    with _lock:
        entry = _store.setdefault(thread_id, {
            "stage":      "url_discovery",
            "message":    STAGE_MESSAGES["url_discovery"],
            "detail":     "",
            "current":    0,
            "total":      0,
            "updated_at": now,
            "candidates": [],
            "branches":   {},
        })
        branches = entry.setdefault("branches", {})
        branches[branch] = status
        entry["updated_at"] = now


def init_candidates(thread_id: str, candidates: list[dict]) -> None:
    """
    candidate별 진행 상태 슬롯을 초기화한다(C-1).

    candidates 인자는 [{"candidate_id": str, "label": str}, ...] 형태.
    """
    now = _now_iso()
    init_entries = [
        {
            "candidate_id": c["candidate_id"],
            "label":        c.get("label", c["candidate_id"]),
            "stage":        "pending",
            "status":       "pending",
            "primary_url":  None,
            "validated":    None,
            "elapsed_ms":   None,
            "updated_at":   now,
        }
        for c in candidates
    ]
    with _lock:
        entry = _store.setdefault(thread_id, {
            "stage":      "url_discovery",
            "message":    STAGE_MESSAGES["url_discovery"],
            "detail":     "",
            "current":    0,
            "total":      len(init_entries),
            "updated_at": now,
            "candidates": [],
            "branches":   {},
        })
        entry["candidates"] = init_entries
        entry["total"]      = len(init_entries)
        entry["updated_at"] = now
        entry.setdefault("branches", {})


def update_candidate(
    thread_id: str,
    candidate_id: str,
    *,
    stage: str | None = None,
    status: str | None = None,
    primary_url: str | None = None,
    validated: bool | None = None,
    elapsed_ms: int | None = None,
) -> None:
    """
    특정 candidate의 진행 이벤트를 갱신한다(C-1).

    None으로 전달된 필드는 기존 값을 유지한다.
    candidate가 init_candidates에서 사전 등록되지 않은 경우 자동으로 추가한다.
    """
    now = _now_iso()
    with _lock:
        entry = _store.setdefault(thread_id, {
            "stage":      "url_discovery",
            "message":    STAGE_MESSAGES["url_discovery"],
            "detail":     "",
            "current":    0,
            "total":      0,
            "updated_at": now,
            "candidates": [],
            "branches":   {},
        })
        cands: list[dict] = entry.setdefault("candidates", [])
        entry.setdefault("branches", {})

        for c in cands:
            if c["candidate_id"] == candidate_id:
                if stage       is not None: c["stage"]       = stage
                if status      is not None: c["status"]      = status
                if primary_url is not None: c["primary_url"] = primary_url
                if validated   is not None: c["validated"]   = validated
                if elapsed_ms  is not None: c["elapsed_ms"]  = elapsed_ms
                c["updated_at"] = now
                break
        else:
            cands.append({
                "candidate_id": candidate_id,
                "label":        candidate_id,
                "stage":        stage or "pending",
                "status":       status or "pending",
                "primary_url":  primary_url,
                "validated":    validated,
                "elapsed_ms":   elapsed_ms,
                "updated_at":   now,
            })

        # current = 완료(done/failed) candidate 수
        entry["current"]    = sum(
            1 for c in cands if c.get("status") in ("done", "failed")
        )
        entry["updated_at"] = now


def get_progress(thread_id: str) -> dict | None:
    """thread_id의 현재 진행 상태를 반환한다. 없으면 None."""
    with _lock:
        return _store.get(thread_id)


def clear_progress(thread_id: str) -> None:
    """invoke 완료(또는 interrupt) 후 저장소에서 해당 thread 항목을 제거한다."""
    with _lock:
        _store.pop(thread_id, None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
