"""
server/graph/api.py
--------------------
LangGraph 파이프라인을 HTTP로 노출하는 FastAPI 서버.

Express가 pythonServer.js를 통해 이 서버를 subprocess로 기동하고,
/invoke 엔드포인트를 통해 그래프를 실행·재개한다.

엔드포인트
----------
POST /invoke
    그래프를 시작하거나 interrupt() 이후 재개한다.
    - 신규 시작: { thread_id, raw_query }
    - interrupt 재개: { thread_id, resume: <edited_form> }

GET  /state/{thread_id}
    지정된 thread의 현재 그래프 상태를 반환한다.

GET  /health
    서버 기동 여부 확인용. pythonServer.js가 이 응답을 감지한다.

interrupt 흐름
--------------
1. Express POST /invoke { raw_query, thread_id }
   → graph.invoke() 실행
   → human_review_node의 interrupt() 에서 중단
   → 응답: { is_interrupted: true, interrupt_value: <query_intake_output> }

2. 사용자가 폼 수정 후 완료
   Express POST /invoke { thread_id, resume: edited_form }
   → graph.invoke(Command(resume=edited_form))
   → human_review_node 재개 → 나머지 노드 실행 → END
   → 응답: { is_interrupted: false, state: <최종 상태> }

실행 방법 (pythonServer.js가 자동 실행):
    uvicorn server.graph.api:app --host 127.0.0.1 --port 8001
"""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command
from pydantic import BaseModel

from server.graph.graph import compiled_graph
from server.graph.progress_store import clear_progress, get_progress

logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube Analysis LangGraph Server",
    description="LangGraph 파이프라인 HTTP 인터페이스",
    version="0.1.0",
)

# Express(localhost)에서의 요청을 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 요청/응답 모델 ─────────────────────────────────────────────────────────────

class InvokeRequest(BaseModel):
    thread_id: str
    raw_query: str | None = None   # 신규 시작 시 필수
    resume: Any = None             # interrupt 재개 시 필수 (edited_form dict)


class InvokeResponse(BaseModel):
    thread_id:       str
    is_interrupted:  bool
    interrupt_value: Any           # interrupt() 호출 시 전달된 값 (query_intake_output)
    next_nodes:      list[str]
    state:           dict[str, Any]


# ── 엔드포인트 ─────────────────────────────────────────────────────────────────

@app.post("/invoke", response_model=InvokeResponse)
def invoke_graph(req: InvokeRequest) -> InvokeResponse:
    """
    그래프를 시작하거나 interrupt 이후 재개한다.

    신규 시작: raw_query 필수, resume은 null.
    재개:      resume 필수, raw_query는 무시.

    ⚠️ 동기 def로 선언한 이유 (중요)
    --------------------------------
    compiled_graph.invoke()는 LangGraph의 동기 블로킹 호출이며, 한 번 실행되면
    interrupt 또는 END에 도달할 때까지(수십 초~수 분) 반환되지 않는다.
    이 함수를 async def로 선언하면 FastAPI 이벤트 루프가 그 시간 동안 통째로
    정지하여 /progress/{thread_id} 폴링까지 응답 불능 상태가 된다.

    sync def로 선언하면 FastAPI가 자동으로 별도 threadpool worker에서 실행해
    이벤트 루프를 해방하며, /progress 폴링이 1.5초 주기로 정상 응답할 수 있다.
    이는 C-1 per-candidate 진행 이벤트가 실시간으로 UI에 흘러가는 전제 조건이다.
    """
    config = {"configurable": {"thread_id": req.thread_id}}

    try:
        if req.resume is not None:
            # ── interrupt 재개 ───────────────────────────────────────────────
            logger.info("invoke: 재개 요청 (thread_id=%s)", req.thread_id)
            compiled_graph.invoke(Command(resume=req.resume), config=config)

        else:
            # ── 신규 시작 ────────────────────────────────────────────────────
            if not req.raw_query:
                raise HTTPException(
                    status_code=400,
                    detail="신규 시작 시 raw_query가 필요합니다.",
                )
            logger.info(
                "invoke: 신규 시작 (thread_id=%s, raw_query=%r)",
                req.thread_id, req.raw_query,
            )
            compiled_graph.invoke(
                {
                    "raw_query":  req.raw_query,
                    "run_id":     req.thread_id,
                    "request_id": req.thread_id,
                },
                config=config,
            )

    except Exception as exc:
        logger.exception("invoke 오류 (thread_id=%s)", req.thread_id)
        clear_progress(req.thread_id)   # 오류 시에도 진행 상태 정리
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # invoke 완료(interrupt 포함) 후 진행 상태 정리
    clear_progress(req.thread_id)

    # ── 현재 상태 조회 ───────────────────────────────────────────────────────
    graph_state   = compiled_graph.get_state(config)
    is_interrupted = bool(graph_state.next)

    # interrupt() 에 전달된 값 추출
    interrupt_value = None
    if is_interrupted:
        for task in graph_state.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                interrupt_value = task.interrupts[0].value
                break

    return InvokeResponse(
        thread_id=req.thread_id,
        is_interrupted=is_interrupted,
        interrupt_value=interrupt_value,
        next_nodes=list(graph_state.next),
        state=dict(graph_state.values),
    )


@app.get("/progress/{thread_id}")
async def get_pipeline_progress(thread_id: str) -> dict:
    """
    지정된 thread의 현재 파이프라인 진행 상태를 반환한다.

    프런트엔드가 1~2초 간격으로 폴링해 URL 탐색·검증 단계를 실시간으로 표시한다.
    invoke가 완료되거나 interrupt가 발생하면 저장소가 비워지므로 null이 반환된다.

    응답 예시
    ----------
    진행 중 (C-1 candidate별 이벤트 포함):
        {
          "thread_id": "abc",
          "progress": {
            "stage":      "url_discovery",
            "message":    "URL 탐색 중",
            "detail":     "5개 항목 LLM 검증 중",
            "current":    3,
            "total":      5,
            "updated_at": "2026-05-13T12:34:56Z",
            "candidates": [
              {
                "candidate_id": "own_tossbnk",
                "label":        "토스뱅크 토스뱅크 카드",
                "stage":        "done",     // pending|brave|fast_path|llm|http|done|failed
                "status":       "done",     // pending|in_progress|done|failed
                "primary_url":  "https://tossbank.com/...",
                "validated":    true,
                "elapsed_ms":   3210,
                "updated_at":   "2026-05-13T12:34:55Z"
              }
            ]
          }
        }

    프런트엔드는 candidates 배열을 사용해 candidate별 진행 칩(■■■□□)을 그려
    사용자에게 단위 완료 피드백을 제공한다(C-1·C-2).

    완료 또는 interrupt 이후:
        { "thread_id": "abc", "progress": null }
    """
    return {
        "thread_id": thread_id,
        "progress":  get_progress(thread_id),
    }


@app.get("/state/{thread_id}")
async def get_graph_state(thread_id: str) -> dict:
    """지정된 thread의 현재 그래프 상태를 반환한다."""
    config = {"configurable": {"thread_id": thread_id}}
    try:
        graph_state = compiled_graph.get_state(config)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "thread_id": thread_id,
        "state":     dict(graph_state.values),
        "next":      list(graph_state.next),
    }


@app.get("/health")
async def health() -> dict:
    """서버 기동 확인. pythonServer.js가 이 응답을 감지해 resolve()한다."""
    return {"status": "ok"}
