import { useState } from 'react';
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
    return 'review';   // interrupt #1: display_fields 기반
  }

  function handleIntakeComplete(data) {
    if (data.thread_id) setThreadId(data.thread_id);
    setIntakeResult(data);
    const next = resolvePage(data);
    if (next === 'critical_error') {
      setCriticalError(extractCriticalError(data));
    } else if (next === 'result') {
      setApproveResult(data);
    }
    setPage(next);
  }

  function handleApproved(data) {
    setIntakeResult(data);
    const next = resolvePage(data);
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
        />
      )}

      {page === 'result' && (
        <ResultView
          result={approveResult}
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
