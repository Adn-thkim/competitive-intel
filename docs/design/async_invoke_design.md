# 비동기 /invoke 최소 스코프 설계 (AI)

> - **상태**: DRAFT — 2026-06-25.
> - **범위**: **리포트 생성 단계(feature_selection 이후 → END)만 비동기.** 앞 3개 interrupt
>   (human_review·competitor_selection·url_retry)는 빠르므로 **동기 유지**.
> - **목표**: 리포트 단계의 HTTP 유실(`fetch failed`·30분 천장·연결 끊김) 제거 +
>   장시간 실행 허용으로 **MAX_ITEMS 재상향** 가능.

## 1. 현 구조(전제)

- `/invoke`는 **sync def** → FastAPI가 threadpool worker에서 실행해 이벤트 루프는 비차단
  (그래서 `/progress` 폴링이 동작). **그러나 HTTP 응답은 `compiled_graph.invoke()`가
  interrupt/END까지 끝나야 반환** → 클라이언트·Express가 그 시간 내내 대기 → 30분
  dispatcher·연결 끊김에 결과 유실.
- `/state/{thread_id}`는 `get_state`로 MemorySaver 체크포인트 조회 가능.
- 프런트 `App.jsx`는 `analysisRunning` 중 `/api/state`를 폴링(이미 존재).

## 2. 설계

### AI-D1 백그라운드 트리거(국소화)
- `InvokeRequest`에 `background: bool = False` 추가.
- 프런트는 **마지막 approve(feature_selection 제출)에만 `background=true`** 전송.
  앞 3개 resume·신규 시작은 `false`(동기 유지) → 변경 표면 최소화.

### AI-D2 작업 레지스트리
- 모듈 전역 `_JOBS: dict[str, dict]` (lock 보호).
  `{thread_id: {"status": "running"|"done"|"error", "error": str|None, "started_at", "finished_at"}}`

### AI-D3 `/invoke` 분기
- `resume is not None and background`:
  1. 이미 `running`이면 409 거부(이중 실행 가드).
  2. `_JOBS[tid] = running`.
  3. **데몬 스레드**로 `_run_resume(tid, resume)` 시작.
  4. **즉시 반환**: `{thread_id, status:"running", is_interrupted:false}` (state 생략/경량).
- 그 외(신규 시작·동기 resume): **현행 동기 경로 그대로**.

### AI-D4 백그라운드 러너
```python
def _run_resume(tid, resume):
    cfg = {"configurable": {"thread_id": tid}}
    try:
        compiled_graph.invoke(Command(resume=resume), config=cfg)
        _set_job(tid, "done")
    except Exception as e:
        logger.exception("async resume 실패 %s", tid)
        _set_job(tid, "error", str(e))
    finally:
        clear_progress(tid)
```
- 데몬 스레드 사용(threadpool 고갈·응답 차단 회피). 내부 CLI subprocess 호출은 스레드에서 정상.

### AI-D5 `/state` 확장
- `/state` 응답에 `job_status` 추가: `{"status","error"}` (동기로 끝난 thread면 없음/null).
- **완료 판별(프런트)**: `job_status=="done"` 또는 `next==[] && state.report_outputs` 존재.
- **오류 판별**: `job_status=="error"` → `error` 메시지 노출.

### AI-D6 Express(`analysisRouter.js`)
- `/api/approve`: body의 `background`를 그대로 Python `/invoke`에 전달. 응답이 즉시 오므로
  long-timeout dispatcher는 무관(유지해도 무해, 선택적 정리).
- `/api/state` 프록시는 그대로(이제 `job_status` 포함 전달).

### AI-D7 프런트(`App.jsx` + `FeatureSelectionPage`)
- feature_selection 제출 → `POST /api/approve {background:true}` → 즉시 ack →
  `analysisRunning=true`, "리포트 생성 중" 화면 진입.
- **기존 `/api/state` 폴링**이 `job_status`를 보고: `running`→유지, `done`→`report_outputs`로
  리포트 렌더, `error`→에러 표시. (무한 폴링 방지: done/error에서 정지)
- 앞 3개 interrupt 페이지: **무변경(동기)**.

## 3. 엣지·리스크

- **MemorySaver 휘발**: 백그라운드 실행 중 서버 재시작 → 작업·`_JOBS` 모두 유실(장시간일수록
  위험). → **SqliteSaver 전환은 별도 검토**(범위 외, 권장 후속).
- **예외 가시화**: 데몬 스레드 예외는 `_JOBS=error`로 기록(삼켜짐 방지) → UI가 무한 대기 안 함.
- **이중 실행**: 같은 tid running 중 재요청 409.
- **MAX_ITEMS 상향 시 CLI 청크 실패**: async는 전체 유실만 막을 뿐, CLI 레이트·300s 청크
  실패는 별개(부분 유실). 깔끔한 대량 처리는 ABSA 엔진 전환(후속)이 필요.

## 4. 변경 파일

- `server/graph/api.py`: `InvokeRequest.background`, `_JOBS`/`_set_job`, `_run_resume`,
  `/invoke` background 분기, `/state` 확장.
- `server/routes/analysisRouter.js`: `background` 전달(+dispatcher 정리 선택).
- `client/src/App.jsx`(+`FeatureSelectionPage.jsx`): 최종 approve `background=true`,
  폴링의 done/error 처리.

## 5. 비변경(절감 요인)
앞 3개 interrupt 흐름 · `/progress` · `/api/state` 폴링 골격 · CORS/포트.

## 6. 검증 기준
- 정상: 큰 MAX_ITEMS로 리포트 단계 수십 분 → **유실 없이 완료·렌더**.
- 실패: 백그라운드 예외 → `job_status=error` → UI 에러 노출.
- 재시작: 진행 중 재시작 → job 유실 재현(한계 명시) → SqliteSaver 필요성 근거.
