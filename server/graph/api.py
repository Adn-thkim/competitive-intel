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
async def invoke_graph(req: InvokeRequest) -> InvokeResponse:
    """
    그래프를 시작하거나 interrupt 이후 재개한다.

    신규 시작: raw_query 필수, resume은 null.
    재개:      resume 필수, raw_query는 무시.
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
    진행 중:
        {
          "thread_id": "abc",
          "progress": {
            "stage":      "url_validation",
            "message":    "URL 검증 중",
            "detail":     "9개 URL 병렬 검증",
            "current":    0,
            "total":      9,
            "updated_at": "2026-05-01T12:34:56Z"
          }
        }

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
