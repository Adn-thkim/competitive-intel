import { useEffect, useState } from 'react';
import SearchPage from './components/SearchPage';
import ReviewForm from './components/ReviewForm';
import CompetitorSelectionPage from './components/CompetitorSelectionPage';
import UrlRetryPage from './components/UrlRetryPage';
import FeatureSelectionPage from './components/FeatureSelectionPage';
import ResultView from './components/ResultView';
import CriticalErrorPage from './components/CriticalErrorPage';

/**
 * 앱 상태 머신
 * -----------
 *   'search'               → 초기 검색 화면
 *   'review'               → AI 초안 검토·수정 폼        (interrupt #1)
 *   'competitor_selection' → 경쟁사 선택 UI              (interrupt #2)
 *   'url_retry'            → URL 탐색 실패 재시도 화면    (interrupt #3, 선택적)
 *   'feature_selection'    → 분석 항목 선택 UI            (interrupt #4)
 *   'result'               → 파이프라인 최종 결과 화면
 *   'critical_error'       → 파이프라인 강제 종료 화면
 *                             (own_* URL 미검증 등 치명적 오류)
 *
 * interrupt 구분 (interrupt_value.type 필드)
 * ------------------------------------------
 *   interrupt #1 (human_review_node):         display_fields 키 존재 (type 없음)
 *   interrupt #2 (competitor_selection_node): type === 'competitor_selection'
 *   interrupt #3 (url_retry_node):            type === 'url_retry'
 *   interrupt #4 (feature_selection_node):    type === 'feature_selection'
 *
 * critical_error 처리
 * -------------------
 *   critical_error는 InvokeResponse.state 안에 담겨 온다 (data.state.critical_error).
 *   data.critical_error(최상위)가 아니므로 반드시 data.state?.critical_error 로 접근해야 한다.
 *   is_interrupted 여부와 무관하게 'critical_error' 페이지로 라우팅한다.
 *   → 신뢰할 수 없는 분석 결과는 분석을 하지 않는 것보다 치명적이다.
 */
export default function App() {
  const [page, setPage]                   = useState('search');
  const [threadId, setThreadId]           = useState(null);
  const [intakeResult, setIntakeResult]   = useState(null);
  const [approveResult, setApproveResult] = useState(null);
  const [criticalError, setCriticalError] = useState(null);

  // ── v0.12.4 점진 렌더링 (progressive report tabs) ─────────────────────────
  // feature_selection 재개 직후 graph 가 END 까지 도는 동안, LangGraph 체크포인터가
  // 노드 완료마다 갱신하는 state 를 GET /api/state/:threadId 로 폴링한다.
  // report_outputs.comparison_matrix 가 보이는 즉시 결과 페이지로 전환하고,
  // 이후 리포트 키가 추가될 때마다 ResultView 의 탭이 점진 활성화된다.
  const [analysisRunning, setAnalysisRunning] = useState(false);
  const [liveState, setLiveState]             = useState(null);
  // 반응 파이프라인 #5 검토 대기 여부 — true 면 ResultView 의 '고객 반응 인사이트' 탭에서 검토 화면을 띄운다.
  const [reactionReviewPending, setReactionReviewPending] = useState(false);

  // #5 검토 진입: 결과 화면(ResultView)으로 가되 반응 탭을 검토 상태로 표시(비교 매트릭스와 병존).
  function enterReactionReview(stateObj) {
    if (stateObj) setLiveState(stateObj);
    setReactionReviewPending(true);
    setAnalysisRunning(false);
    setPage('result');
  }

  // #5 확인(동기 재개) 응답 처리 — 검토 종료 + 최종 state(reaction_report_output 포함) 반영.
  function handleReactionApproved(data) {
    if (extractCriticalError(data)) {
      setReactionReviewPending(false);
      setAnalysisRunning(false);
      setCriticalError(extractCriticalError(data));
      setPage('critical_error');
      return;
    }
    // 재개했는데 여전히 #5 에 멈춰 있으면(is_interrupted) 리포트로 넘어가지 못한 것 —
    // 빈 '준비 중' 데드엔드 대신 검토 화면을 다시 띄운다(원인 진단 가능하도록).
    if (data?.is_interrupted) {
      enterReactionReview(data.state);
      return;
    }
    setReactionReviewPending(false);
    setAnalysisRunning(false);
    if (data?.state) setLiveState(data.state);
    setApproveResult(data);
    setPage('result');
  }

  useEffect(() => {
    if (!analysisRunning || !threadId || approveResult) return undefined;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/state/${encodeURIComponent(threadId)}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data?.state) setLiveState(data.state);

        // 반응 파이프라인 #5 interrupt(reaction_axis_review) 감지 — /state 는 interrupt_value 를
        // 안 주지만 next 에 'reaction_review' 가 있으면 그 지점에 멈춘 것. 결과 화면의 반응 탭에서 검토.
        if (Array.isArray(data?.next) && data.next.includes('reaction_review')) {
          enterReactionReview(data.state);    // 폴링 종료 + 반응 탭 검토 상태
          return;
        }

        // 비동기 리포트 단계 완료/오류 처리 (async_invoke_design.md AI-D7)
        const js = data?.job_status;
        if (js?.status === 'done') {
          const finalData = { thread_id: threadId, is_interrupted: false, state: data.state };
          setAnalysisRunning(false);   // 폴링 종료
          if (extractCriticalError(finalData)) {
            setCriticalError(extractCriticalError(finalData));
            setPage('critical_error');
          } else {
            setApproveResult(finalData);
            setPage('result');
          }
        } else if (js?.status === 'error') {
          setAnalysisRunning(false);
          setCriticalError(js.error || '리포트 생성 중 오류가 발생했습니다.');
          setPage('critical_error');
        }
      } catch {
        /* 일시적 폴링 실패는 무시 — 다음 주기에 재시도 */
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [analysisRunning, threadId, approveResult]);

  // 첫 리포트가 보이는 즉시 결과 페이지로 전환(점진 렌더). 리포트 종류 무관(예: reaction_insight 단독).
  useEffect(() => {
    if (analysisRunning && page === 'feature_selection'
        && liveState?.report_outputs
        && Object.keys(liveState.report_outputs).length > 0) {
      setPage('result');
    }
  }, [analysisRunning, page, liveState]);

  /**
   * InvokeResponse에서 critical_error 값을 안전하게 추출한다.
   *
   * InvokeResponse 구조:
   *   { thread_id, is_interrupted, interrupt_value, next_nodes, state: { critical_error, ... } }
   *
   * critical_error는 state 객체 안에 있으므로 data.state?.critical_error 로 읽어야 한다.
   * data.critical_error 는 항상 undefined — 이를 사용하면 CriticalErrorPage가 절대 표시되지 않는다.
   */
  function extractCriticalError(data) {
    return data.state?.critical_error ?? data.critical_error ?? null;
  }

  /**
   * API 응답으로부터 다음 페이지를 결정한다.
   * critical_error는 interrupt 여부보다 우선한다.
   */
  function resolvePage(data) {
    // ── 치명적 오류: 파이프라인 강제 종료 ────────────────────────────────────
    if (extractCriticalError(data)) return 'critical_error';

    // ── 정상 interrupt 분기 ──────────────────────────────────────────────────
    if (!data.is_interrupted) return 'result';
    const type = data.interrupt_value?.type;
    if (type === 'competitor_selection') return 'competitor_selection';
    if (type === 'url_retry')            return 'url_retry';
    if (type === 'feature_selection')    return 'feature_selection';
    // interrupt #5(reaction_review): payload 는 review_type 키를 쓴다(type 아님).
    if (data.interrupt_value?.review_type === 'reaction_axis_review') return 'reaction_axis_review';
    return 'review';   // interrupt #1: display_fields 기반
  }

  function handleIntakeComplete(data) {
    if (data.thread_id) setThreadId(data.thread_id);
    setIntakeResult(data);
    const next = resolvePage(data);
    if (next === 'reaction_axis_review') { enterReactionReview(data.state); return; }
    if (next === 'critical_error') {
      setCriticalError(extractCriticalError(data));
    } else if (next === 'result') {
      setApproveResult(data);
    }
    setPage(next);
  }

  function handleApproved(data) {
    setAnalysisRunning(false);   // 최종 응답 도착 — 폴링 종료
    setIntakeResult(data);
    const next = resolvePage(data);
    if (next === 'reaction_axis_review') { enterReactionReview(data.state); return; }
    if (next === 'critical_error') {
      setCriticalError(extractCriticalError(data));
    } else if (next === 'result') {
      setApproveResult(data);
    }
    setPage(next);
  }

  function handleReset() {
    setPage('search');
    setIntakeResult(null);
    setApproveResult(null);
    setThreadId(null);
    setCriticalError(null);
    setAnalysisRunning(false);
    setLiveState(null);
    setReactionReviewPending(false);
  }

  return (
    <>
      {page === 'search' && (
        <SearchPage onIntakeComplete={handleIntakeComplete} />
      )}

      {page === 'review' && intakeResult && (
        <ReviewForm
          intakeResult={intakeResult}
          onApproved={handleApproved}
          onReset={handleReset}
        />
      )}

      {page === 'competitor_selection' && intakeResult && (
        <CompetitorSelectionPage
          intakeResult={intakeResult}
          threadId={threadId}
          onApproved={handleApproved}
          onReset={handleReset}
        />
      )}

      {page === 'url_retry' && intakeResult && (
        <UrlRetryPage
          intakeResult={intakeResult}
          threadId={threadId}
          onApproved={handleApproved}
          onReset={handleReset}
        />
      )}

      {page === 'feature_selection' && intakeResult && (
        <FeatureSelectionPage
          intakeResult={intakeResult}
          threadId={threadId}
          onApproved={handleApproved}
          onReset={handleReset}
          onStarted={() => { setLiveState(null); setAnalysisRunning(true); }}
          onFailed={() => setAnalysisRunning(false)}
        />
      )}

      {page === 'result' && (
        <ResultView
          result={approveResult ?? (liveState ? { state: liveState } : null)}
          reportsRunning={analysisRunning && !approveResult}
          reactionReviewPending={reactionReviewPending}
          threadId={threadId}
          onReactionApproved={handleReactionApproved}
          onReset={handleReset}
        />
      )}

      {page === 'critical_error' && (
        <CriticalErrorPage
          message={criticalError}
          onReset={handleReset}
        />
      )}
    </>
  );
}
