import { useState, useEffect } from 'react';

/**
 * UrlRetryPage
 * ------------
 * URL 검증 실패 항목 재시도 화면.
 *
 * Phase 1 (is_final=false)
 * -------------------------
 * - 실패 항목 카드 + 수동 URL 입력 (선택)
 * - "✏️ 직접 입력한 URL로 재시도"   : 입력된 URL로 검증 / 빈 카드는 LLM 재탐색
 * - "🔄 재탐색하여 재시도"           : 모든 카드 LLM 재탐색 (수동 입력 무시)
 *
 * Phase 2 (is_final=true)
 * ------------------------
 * - 재시도 후에도 실패한 항목 표시
 * - 카드별 action_case에 따른 버튼 제공:
 *   case1    (official comp_*): "이 경쟁사 분석에서 제거" 토글
 *   case2_1  (reference 부분): 실패 URL별 "이 URL 제거" 체크박스
 *   case2_2  (reference 전체): "이 대안 수단 분석에서 제거" 토글
 *   null     (own_*):          수동 URL 입력만 허용
 * - 수동 URL 입력은 항상 제공 (제거 선택 시 비활성화)
 * - "확인" 버튼으로 모든 결정 일괄 전송
 *
 * Props
 * -----
 * - intakeResult : 가장 최근 /api/approve 응답
 * - threadId     : LangGraph thread_id
 * - onApproved   : (data) => void
 * - onReset      : () => void
 */
export default function UrlRetryPage({ intakeResult, threadId, onApproved, onReset }) {
  const iv          = intakeResult?.interrupt_value ?? {};
  const isFinal     = iv.is_final === true;
  const failed      = iv.failed_sources ?? [];
  const autoLlmTried = iv.auto_llm_tried === true;

  const sharedProps = { failed, threadId, onApproved, onReset };

  return isFinal
    ? <Phase2View {...sharedProps} />
    : <Phase1View {...sharedProps} autoLlmTried={autoLlmTried} threadId={threadId} />;
}


/* ═══════════════════════════════════════════════════════════ Phase 1 ══════ */

/** HTTP 상태코드 → 검증 실패 유형 메타 (Phase 1 / Phase 2 카드 공통 사용) */
function classifyFailStatus(status) {
  if (status == null)                     return { icon: '⚡', text: '연결 실패 (타임아웃 또는 연결 거부)',  cls: 'bg-gray-50 border-gray-200 text-gray-600' };
  if (status === 403)                     return { icon: '🔒', text: `접근 제한 (403 Forbidden)`,          cls: 'bg-purple-50 border-purple-200 text-purple-700' };
  if (status === 404)                     return { icon: '🔍', text: `페이지 없음 (404 Not Found)`,         cls: 'bg-orange-50 border-orange-200 text-orange-700' };
  if (status >= 400 && status < 500)      return { icon: '⚠️', text: `클라이언트 오류 (${status})`,         cls: 'bg-yellow-50 border-yellow-200 text-yellow-700' };
  if (status >= 500)                      return { icon: '🔴', text: `서버 오류 (${status})`,               cls: 'bg-red-50 border-red-200 text-red-700' };
  return                                         { icon: '❓', text: `검증 실패 (HTTP ${status})`,           cls: 'bg-gray-50 border-gray-200 text-gray-600' };
}

function Phase1View({ failed, threadId, onApproved, onReset, autoLlmTried = false }) {
  const [manualUrls, setManualUrls] = useState(
    Object.fromEntries(failed.map(f => [f.candidate_id, '']))
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError]           = useState('');

  // 제출 중이면 파이프라인 로딩 화면으로 전환 (threadId 전달 → 실시간 진행 표시)
  if (submitting) return <PipelineLoadingScreen threadId={threadId} />;

  const hasAnyInput = Object.values(manualUrls).some(v => v.trim());

  /** withManual=true  → 입력된 URL 전송 (빈 카드는 LLM 재탐색)
   *  withManual=false → 모두 빈 문자열 전송 → 전체 LLM 재탐색 */
  async function submit(withManual) {
    setSubmitting(true);
    setError('');
    const payload = withManual
      ? manualUrls
      : Object.fromEntries(failed.map(f => [f.candidate_id, '']));

    try {
      const res = await fetch('/api/approve', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          thread_id: threadId,
          resume:    { manual_urls: payload },
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? `서버 오류 (HTTP ${res.status})`);
      onApproved(data);
    } catch (err) {
      setError(err.message ?? '알 수 없는 오류가 발생했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-2xl mx-auto">

        {/* 헤더 */}
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-2xl">⚠️</span>
            <h1 className="text-2xl font-bold text-gray-900">URL 탐색 실패</h1>
          </div>
          <p className="text-sm text-gray-500 mt-1">
            {failed.length}개 항목의 공식 URL을 자동으로 확인하지 못했습니다.
            직접 URL을 입력하거나, AI가 다른 URL을 재탐색하도록 할 수 있습니다.
          </p>
          {autoLlmTried ? (
            <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800">
              <strong>🤖 AI 자동 재탐색 완료:</strong> 새로운 URL을 탐색했지만 유효한 주소를 찾지 못했습니다.
              직접 URL을 입력하거나, AI가 한 번 더 다른 주소를 탐색하도록 할 수 있습니다.
            </div>
          ) : (
            <div className="mt-3 p-3 bg-blue-50 border border-blue-200 rounded-lg text-xs text-blue-700">
              <strong>💡 재시도 방식 안내:</strong> URL을 직접 입력한 항목은 해당 URL로 검증하고,
              입력하지 않은 항목은 AI가 새로운 URL을 탐색합니다.
            </div>
          )}
        </div>

        {/* 실패 항목 카드 */}
        <div className="flex flex-col gap-4 mb-6">
          {failed.map(f => (
            <Phase1Card
              key={f.candidate_id}
              item={f}
              value={manualUrls[f.candidate_id] ?? ''}
              onChange={v => {
                setManualUrls(prev => ({ ...prev, [f.candidate_id]: v }));
                setError('');
              }}
              disabled={submitting}
            />
          ))}
        </div>

        {/* 오류 메시지 */}
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {error}
          </div>
        )}

        {/* 버튼 영역 */}
        <div className="flex flex-col gap-3">
          <button
            onClick={() => submit(true)}
            disabled={submitting}
            className="w-full py-3 text-sm font-semibold rounded-xl transition-colors bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? '처리 중…' : hasAnyInput ? '✏️ 직접 입력한 URL로 재시도' : '✏️ 재시도 (빈 항목은 AI 재탐색)'}
          </button>

          <button
            onClick={() => submit(false)}
            disabled={submitting}
            className="w-full py-3 text-sm font-semibold text-gray-700 bg-white border-2 border-gray-300 rounded-xl hover:bg-gray-50 hover:border-gray-400 disabled:opacity-50 transition-colors"
          >
            {submitting ? '처리 중…' : '🔄 모두 재탐색하여 재시도'}
          </button>

          <button
            onClick={onReset}
            disabled={submitting}
            className="w-full py-2.5 text-sm text-gray-400 hover:text-gray-600 disabled:opacity-50"
          >
            처음으로 돌아가기
          </button>
        </div>

      </div>
    </div>
  );
}

function Phase1Card({ item, value, onChange, disabled }) {
  const isOfficial  = item.source_type === 'official';
  const isReference = item.source_type === 'reference';

  return (
    <div className="bg-white rounded-2xl border border-red-200 shadow-sm p-5">
      {/* 카드 헤더 */}
      <div className="flex flex-wrap items-start gap-2 mb-3">
        <div className="w-9 h-9 rounded-full bg-red-100 flex items-center justify-center text-red-600 font-bold text-sm shrink-0">
          {isOfficial ? (item.brand ?? '?')[0] : (item.method_name ?? '?')[0]}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-gray-900 text-sm">
              {isOfficial ? item.product_name : item.method_name}
            </span>
            <span className={[
              'text-xs font-medium px-2 py-0.5 rounded-full',
              isOfficial ? 'bg-blue-100 text-blue-800' : 'bg-teal-100 text-teal-800',
            ].join(' ')}>
              {isOfficial ? '브랜드 상품' : '기능적 대안'}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700 font-medium">
              URL 탐색 실패
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            {isOfficial ? item.brand : item.provider_type}
            &nbsp;·&nbsp;
            <code className="font-mono text-gray-400 text-xs">{item.candidate_id}</code>
          </p>
        </div>
      </div>

      {/* 자동 재탐색 검증 실패 유형 배지 */}
      {isOfficial && (() => {
        const fail = classifyFailStatus(item.auto_retry_fail_status ?? null);
        return (
          <div className={`mb-3 flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-medium ${fail.cls}`}>
            <span>{fail.icon}</span>
            <span>자동 재탐색 검증 실패: {fail.text}</span>
          </div>
        );
      })()}

      {/* 시도한 URL 표시 */}
      {isOfficial && item.tried_url && (
        <div className="mb-3 p-2.5 bg-red-50 rounded-lg border border-red-100">
          <p className="text-xs text-red-600 font-medium mb-0.5">시도한 URL (실패)</p>
          <p className="text-xs text-gray-600 font-mono break-all">{item.tried_url}</p>
          {item.llm_confidence != null && (
            <p className="text-xs text-gray-400 mt-0.5">
              AI 추정 신뢰도: {Math.round(item.llm_confidence * 100)}%
            </p>
          )}
        </div>
      )}
      {isReference && item.failed_ref_urls?.length > 0 && (
        <div className="mb-3 p-2.5 bg-red-50 rounded-lg border border-red-100">
          <p className="text-xs text-red-600 font-medium mb-1">응답 없는 참조 URL</p>
          {item.failed_ref_urls.map((u, i) => (
            <p key={i} className="text-xs text-gray-600 font-mono break-all">{u}</p>
          ))}
        </div>
      )}

      {/* URL 직접 입력 */}
      <div>
        <label className="block text-xs font-medium text-gray-600 mb-1.5">
          {isOfficial ? '공식 URL 직접 입력 (선택 — 미입력 시 AI 재탐색)' : '참조 URL 직접 입력 (선택 — 미입력 시 AI 재탐색)'}
        </label>
        <input
          type="url"
          value={value}
          onChange={e => onChange(e.target.value)}
          disabled={disabled}
          placeholder={isOfficial ? 'https://brand.com/product' : 'https://www.institution.go.kr/...'}
          className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent disabled:bg-gray-50 font-mono placeholder-gray-300"
        />
        {value && !isValidUrl(value) && (
          <p className="text-xs text-red-500 mt-1">유효한 URL 형식이 아닙니다 (https://로 시작해야 합니다).</p>
        )}
      </div>
    </div>
  );
}


/* ═══════════════════════════════════════════════════════════ Phase 2 ══════ */

function Phase2View({ failed, threadId, onApproved, onReset }) {
  // { cid: url }
  const [manualUrls, setManualUrls]     = useState({});
  // { cid: true } — 전체 제거 대상 (case1, case2_2)
  const [removedIds, setRemovedIds]     = useState({});
  // { cid: { url: true } } — 제거할 ref URL (case2_1)
  const [removedRefs, setRemovedRefs]   = useState({});
  const [submitting, setSubmitting]     = useState(false);
  const [error, setError]               = useState('');
  // 제출 시도 여부 — own_* URL 미입력 카드 강조에 사용
  const [submitAttempted, setSubmitAttempted] = useState(false);

  // 제출 중이면 파이프라인 로딩 화면으로 전환 (threadId 전달 → 실시간 진행 표시)
  if (submitting) return <PipelineLoadingScreen threadId={threadId} />;

  function toggleRemoveId(cid) {
    setRemovedIds(prev => ({ ...prev, [cid]: !prev[cid] }));
    // 제거 선택 시 수동 URL 초기화
    setManualUrls(prev => { const n = { ...prev }; delete n[cid]; return n; });
  }

  function toggleRemoveRef(cid, url) {
    setRemovedRefs(prev => {
      const cidMap = { ...(prev[cid] ?? {}) };
      if (cidMap[url]) {
        delete cidMap[url];
      } else {
        cidMap[url] = true;
      }
      return { ...prev, [cid]: cidMap };
    });
  }

  function setManualUrl(cid, url) {
    setManualUrls(prev => ({ ...prev, [cid]: url }));
    // 수동 URL 입력 시 제거 선택 해제
    if (url.trim()) {
      setRemovedIds(prev => { const n = { ...prev }; delete n[cid]; return n; });
    }
    setError('');
  }

  async function submit() {
    setSubmitAttempted(true);

    // ── own_* 항목 URL 필수 입력 검증 ─────────────────────────────────────
    const ownItems = failed.filter(f => f.action_case === null || f.action_case === undefined);
    const missingOwn = ownItems.filter(f => {
      const url = manualUrls[f.candidate_id]?.trim() ?? '';
      return !url || !isValidUrl(url);
    });
    if (missingOwn.length > 0) {
      const names = missingOwn.map(f => f.product_name || f.candidate_id).join(', ');
      setError(`자사 상품(${names})의 URL을 반드시 입력해 주세요. 분석을 계속하려면 유효한 URL이 필요합니다.`);
      return;
    }

    setSubmitting(true);
    setError('');

    const payload = {
      manual_urls: Object.fromEntries(
        Object.entries(manualUrls).filter(([, v]) => v.trim())
      ),
      remove_ids: Object.entries(removedIds)
        .filter(([, v]) => v)
        .map(([cid]) => cid),
      remove_ref_urls: Object.fromEntries(
        Object.entries(removedRefs)
          .map(([cid, urlMap]) => [cid, Object.keys(urlMap)])
          .filter(([, urls]) => urls.length > 0)
      ),
    };

    try {
      const res = await fetch('/api/approve', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          thread_id: threadId,
          resume:    payload,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? `서버 오류 (HTTP ${res.status})`);
      onApproved(data);
    } catch (err) {
      setError(err.message ?? '알 수 없는 오류가 발생했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-2xl mx-auto">

        {/* 헤더 */}
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-2xl">🔴</span>
            <h1 className="text-2xl font-bold text-gray-900">재시도 후에도 URL 탐색 실패</h1>
          </div>
          <p className="text-sm text-gray-500 mt-1">
            재탐색 후에도 {failed.length}개 항목의 URL을 확인하지 못했습니다.
            항목별로 직접 URL을 입력하거나 분석에서 제거하세요.
          </p>
        </div>

        {/* 실패 항목 카드 */}
        <div className="flex flex-col gap-4 mb-6">
          {failed.map(f => (
            <Phase2Card
              key={f.candidate_id}
              item={f}
              manualUrl={manualUrls[f.candidate_id] ?? ''}
              isRemoved={!!removedIds[f.candidate_id]}
              removedRefUrls={removedRefs[f.candidate_id] ?? {}}
              onManualUrlChange={v => setManualUrl(f.candidate_id, v)}
              onToggleRemove={() => toggleRemoveId(f.candidate_id)}
              onToggleRemoveRef={url => toggleRemoveRef(f.candidate_id, url)}
              disabled={submitting}
              submitAttempted={submitAttempted}
            />
          ))}
        </div>

        {/* 오류 메시지 */}
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {error}
          </div>
        )}

        {/* 버튼 영역 */}
        <div className="flex flex-col gap-3">
          <button
            onClick={submit}
            disabled={submitting}
            className="w-full py-3 text-sm font-semibold rounded-xl transition-colors bg-gray-900 text-white hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? '처리 중…' : '✅ 확인 및 분석 계속'}
          </button>

          <button
            onClick={onReset}
            disabled={submitting}
            className="w-full py-2.5 text-sm text-gray-400 hover:text-gray-600 disabled:opacity-50"
          >
            처음으로 돌아가기
          </button>
        </div>

      </div>
    </div>
  );
}

/** retry_fail_reason 배지 설정 */
const FAIL_REASON_META = {
  api_error:     { icon: '🔴', text: 'AI 재탐색 중 오류 발생 (API 오류)',      cls: 'bg-red-50 border-red-200 text-red-700' },
  no_url_found:  { icon: '🔍', text: 'AI가 유효한 URL을 찾지 못했습니다',       cls: 'bg-amber-50 border-amber-200 text-amber-700' },
  manual_failed: { icon: '🔗', text: '입력하신 URL이 응답하지 않았습니다',      cls: 'bg-orange-50 border-orange-200 text-orange-700' },
};

function Phase2Card({
  item,
  manualUrl,
  isRemoved,
  removedRefUrls,
  onManualUrlChange,
  onToggleRemove,
  onToggleRemoveRef,
  disabled,
  submitAttempted = false,
}) {
  const { candidate_id: cid, source_type, action_case, retry_fail_reason, phase1_fail_status } = item;
  const isOfficial   = source_type === 'official';
  const isReference  = source_type === 'reference';
  const isOwnProduct = cid?.startsWith('own_');

  // own_* 항목에서 submit 시도 후 URL 미입력 상태 감지
  const ownUrlMissing = isOwnProduct && submitAttempted && (!manualUrl.trim() || !isValidUrl(manualUrl));

  const cardBorder = isRemoved
    ? 'border-gray-200 opacity-60'
    : 'border-orange-300';

  return (
    <div className={`bg-white rounded-2xl border shadow-sm p-5 transition-opacity ${cardBorder}`}>

      {/* 카드 헤더 */}
      <div className="flex flex-wrap items-start gap-2 mb-4">
        <div className="w-9 h-9 rounded-full bg-orange-100 flex items-center justify-center text-orange-600 font-bold text-sm shrink-0">
          {isOfficial ? (item.brand ?? '?')[0] : (item.method_name ?? '?')[0]}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-gray-900 text-sm">
              {isOfficial ? item.product_name : item.method_name}
            </span>
            <span className={[
              'text-xs font-medium px-2 py-0.5 rounded-full',
              isOfficial ? 'bg-blue-100 text-blue-800' : 'bg-teal-100 text-teal-800',
            ].join(' ')}>
              {isOfficial ? '브랜드 상품' : '기능적 대안'}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 font-medium">
              재탐색 실패
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            {isOfficial ? item.brand : item.provider_type}
            &nbsp;·&nbsp;
            <code className="font-mono text-gray-400 text-xs">{cid}</code>
          </p>
        </div>
      </div>

      {/* 재탐색 실패 원인 배지 */}
      {retry_fail_reason && FAIL_REASON_META[retry_fail_reason] && (
        <div className={`mb-2 flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-medium ${FAIL_REASON_META[retry_fail_reason].cls}`}>
          <span>{FAIL_REASON_META[retry_fail_reason].icon}</span>
          <span>{FAIL_REASON_META[retry_fail_reason].text}</span>
        </div>
      )}

      {/* 재시도 검증 실패 유형 배지 (Phase 1 HTTP 상태) */}
      {isOfficial && (() => {
        const fail = classifyFailStatus(phase1_fail_status ?? null);
        return (
          <div className={`mb-4 flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-medium ${fail.cls}`}>
            <span>{fail.icon}</span>
            <span>재시도 검증 실패: {fail.text}</span>
          </div>
        );
      })()}

      {/* ── case1: 경쟁사 전체 제거 ── */}
      {action_case === 'case1' && (
        <div className="mb-4">
          <button
            onClick={onToggleRemove}
            disabled={disabled}
            className={[
              'w-full flex items-center justify-between px-4 py-3 rounded-xl border-2 text-sm font-medium transition-colors',
              isRemoved
                ? 'bg-red-50 border-red-400 text-red-700'
                : 'bg-white border-gray-200 text-gray-600 hover:border-red-300 hover:text-red-600',
            ].join(' ')}
          >
            <span>🗑️ 이 경쟁사를 분석에서 제거</span>
            <span className={`w-5 h-5 rounded-full border-2 flex items-center justify-center text-xs
              ${isRemoved ? 'bg-red-500 border-red-500 text-white' : 'border-gray-300'}`}>
              {isRemoved && '✓'}
            </span>
          </button>
          {isRemoved && (
            <p className="text-xs text-red-500 mt-1.5 text-center">
              이 항목은 최종 분석에서 제외됩니다.
            </p>
          )}
        </div>
      )}

      {/* ── case2_1: 실패 URL별 제거 ── */}
      {action_case === 'case2_1' && item.failed_ref_urls?.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-gray-600 mb-2">제거할 참조 URL 선택:</p>
          <div className="flex flex-col gap-2">
            {item.failed_ref_urls.map((url, i) => {
              const checked = !!removedRefUrls[url];
              return (
                <label
                  key={i}
                  className={[
                    'flex items-start gap-3 p-2.5 rounded-lg border cursor-pointer transition-colors',
                    checked
                      ? 'bg-red-50 border-red-300'
                      : 'bg-gray-50 border-gray-200 hover:border-red-200',
                    disabled ? 'opacity-50 cursor-not-allowed' : '',
                  ].join(' ')}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => !disabled && onToggleRemoveRef(url)}
                    disabled={disabled}
                    className="mt-0.5 accent-red-500 shrink-0"
                  />
                  <div className="min-w-0">
                    <p className="text-xs font-mono text-gray-600 break-all">{url}</p>
                    {checked && (
                      <p className="text-xs text-red-500 mt-0.5">제거 예정</p>
                    )}
                  </div>
                </label>
              );
            })}
          </div>
        </div>
      )}

      {/* ── case2_2: 기능적 대안 전체 제거 ── */}
      {action_case === 'case2_2' && (
        <div className="mb-4">
          <button
            onClick={onToggleRemove}
            disabled={disabled}
            className={[
              'w-full flex items-center justify-between px-4 py-3 rounded-xl border-2 text-sm font-medium transition-colors',
              isRemoved
                ? 'bg-red-50 border-red-400 text-red-700'
                : 'bg-white border-gray-200 text-gray-600 hover:border-red-300 hover:text-red-600',
            ].join(' ')}
          >
            <span>🗑️ 이 대안 수단을 분석에서 제거</span>
            <span className={`w-5 h-5 rounded-full border-2 flex items-center justify-center text-xs
              ${isRemoved ? 'bg-red-500 border-red-500 text-white' : 'border-gray-300'}`}>
              {isRemoved && '✓'}
            </span>
          </button>
          {isRemoved && (
            <p className="text-xs text-red-500 mt-1.5 text-center">
              이 항목은 최종 분석에서 제외됩니다.
            </p>
          )}
        </div>
      )}

      {/* ── 수동 URL 입력 (action_case=null인 own_* 포함 항상 표시) ── */}
      {!isRemoved && (
        <div>
          <div className="flex items-center gap-1.5 mb-1.5">
            <label className={`block text-xs font-medium ${ownUrlMissing ? 'text-red-600' : 'text-gray-600'}`}>
              {isOfficial ? '공식 URL 직접 입력' : '참조 URL 직접 입력'}
              {isOwnProduct && <span className="ml-1 text-red-500">*</span>}
            </label>
            {!isOwnProduct && action_case && (
              <span className="text-xs text-gray-400">(제거 선택 시 무시됨)</span>
            )}
          </div>
          <input
            type="url"
            value={manualUrl}
            onChange={e => onManualUrlChange(e.target.value)}
            disabled={disabled}
            placeholder={isOfficial ? 'https://brand.com/product' : 'https://www.institution.go.kr/...'}
            className={[
              'w-full px-3 py-2 text-sm border rounded-lg focus:outline-none focus:ring-2 focus:border-transparent disabled:bg-gray-50 font-mono placeholder-gray-300 transition-colors',
              ownUrlMissing
                ? 'border-red-400 focus:ring-red-400'
                : 'border-gray-300 focus:ring-indigo-500',
            ].join(' ')}
          />
          {manualUrl && !isValidUrl(manualUrl) && (
            <p className="text-xs text-red-500 mt-1">
              유효한 URL 형식이 아닙니다 (https://로 시작해야 합니다).
            </p>
          )}
          {ownUrlMissing && !manualUrl && (
            <p className="text-xs text-red-600 font-medium mt-1">
              ⚠️ 자사 상품 URL은 필수입니다. 분석을 계속하려면 입력이 필요합니다.
            </p>
          )}
          {isOwnProduct && !ownUrlMissing && (
            <p className="text-xs text-amber-600 mt-1">
              ℹ️ 자사 상품 URL은 분석에 필수입니다. 정확한 URL을 입력해 주세요.
            </p>
          )}
        </div>
      )}

    </div>
  );
}


/* ══════════════════════════════════════════════════════ 파이프라인 로딩 ══════ */

/**
 * PipelineLoadingScreen
 * ----------------------
 * URL 재시도 제출 후 파이프라인이 재개될 때 표시되는 전체 화면 로딩 UI.
 *
 * 실시간 단계 (progress API 폴링):
 *   url_phase1_llm       → URL 재탐색 중 (LLM / Brave Search)
 *   url_phase1_validation → URL 재검증 중 (HTTP 병렬)
 *
 * 시간 기반 단계 (URL 처리 완료 후 경과 시간으로 추정):
 *   Feature URL 매핑     → 목적별 URL 커버리지 병렬 매핑 (가장 오래 걸림)
 *   Feature Selection 준비 → 분석 항목 선택 화면 구성
 *
 * URL 실시간 단계가 완료(progress=null)되면 elapsed 타이머를 시작한다.
 */

// 실시간 URL 단계 정의
const URL_REAL_STAGES = [
  { id: 'url_phase1_llm',        label: 'URL 재탐색',  desc: 'AI가 실패한 URL을 새로 탐색합니다.' },
  { id: 'url_phase1_validation', label: 'URL 재검증',  desc: '재탐색된 URL의 접근 가능 여부를 확인합니다.' },
];

// 시간 기반 단계 정의 (URL 완료 후 경과 초 기준)
const POST_URL_STAGES = [
  { id: 'feature_url_mapper', label: 'Feature URL 매핑',     desc: '분석 목적별로 URL 커버리지를 병렬 매핑합니다. (가장 오래 걸립니다)', from: 0,   to: 195 },
  { id: 'feature_selection',  label: 'Feature Selection 준비', desc: '분석 항목 선택 화면을 구성합니다.',                                   from: 195, to: 225 },
];

function PipelineLoadingScreen({ threadId }) {
  // 실시간 진행 상태 (progress API)
  const [progress,     setProgress]     = useState(null);
  const [urlDone,      setUrlDone]      = useState(false);   // URL 실시간 단계 완료 여부
  const [seenProgress, setSeenProgress] = useState(false);   // 한 번이라도 progress를 수신했는지
  // 시간 기반 단계용 경과 시간 (URL 완료 후 시작)
  const [elapsed, setElapsed] = useState(0);
  // 총 경과 시간 (헤더 표시용)
  const [totalElapsed, setTotalElapsed] = useState(0);

  // 총 경과 타이머 (항상 실행)
  useEffect(() => {
    const t = setInterval(() => setTotalElapsed(s => s + 1), 1000);
    return () => clearInterval(t);
  }, []);

  // progress API 폴링 (URL 단계가 완료되기 전까지)
  useEffect(() => {
    if (!threadId || urlDone) return;
    let cancelled = false;

    async function poll() {
      if (cancelled) return;
      try {
        const res  = await fetch(`/api/progress/${threadId}`);
        if (!res.ok || cancelled) return;
        const data = await res.json();

        if (data.progress) {
          if (!cancelled) {
            setProgress(data.progress);
            setSeenProgress(true);
          }
        } else if (seenProgress) {
          // 한 번 progress를 받았다가 null → URL 단계 완료
          if (!cancelled) {
            setUrlDone(true);
            setProgress(null);
          }
        }
      } catch { /* 폴링 오류 무시 */ }
    }

    poll();
    const interval = setInterval(poll, 1500);
    return () => { cancelled = true; clearInterval(interval); };
  }, [threadId, urlDone, seenProgress]);

  // URL 완료 후 경과 타이머 시작
  useEffect(() => {
    if (!urlDone) return;
    const t = setInterval(() => setElapsed(s => s + 1), 1000);
    return () => clearInterval(t);
  }, [urlDone]);

  const formatTime = (s) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}분 ${sec}초` : `${sec}초`;
  };

  // URL 실시간 단계: 현재 진행 중인 stage의 index
  const urlActiveIdx = progress
    ? URL_REAL_STAGES.findIndex(s => s.id === progress.stage)
    : (urlDone ? URL_REAL_STAGES.length : -1);  // -1 = 아직 시작 전

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-md">

        {/* 헤더 */}
        <div className="text-center mb-8">
          <div className="flex justify-center mb-4">
            <svg className="animate-spin h-10 w-10 text-indigo-500" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
            </svg>
          </div>
          <h2 className="text-xl font-bold text-gray-900">AI 분석 재개 중</h2>
          <p className="text-sm text-gray-500 mt-1">경과 시간: {formatTime(totalElapsed)}</p>
        </div>

        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm divide-y divide-gray-100">

          {/* ── 실시간 URL 단계 ── */}
          {URL_REAL_STAGES.map((stage, idx) => {
            const isDone    = urlActiveIdx > idx || urlDone;
            const isRunning = !isDone && urlActiveIdx === idx;
            const isPending = !isDone && !isRunning;
            const detail    = isRunning && progress?.detail ? progress.detail : null;

            return (
              <div key={stage.id} className="px-5 py-4 flex items-start gap-3">
                <div className="mt-0.5 shrink-0">
                  {isDone && (
                    <div className="w-5 h-5 rounded-full bg-green-500 flex items-center justify-center">
                      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7"/>
                      </svg>
                    </div>
                  )}
                  {isRunning && <div className="w-5 h-5 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin"/>}
                  {isPending && <div className="w-5 h-5 rounded-full border-2 border-gray-200"/>}
                </div>
                <div className="flex-1 min-w-0">
                  <p className={['text-sm font-semibold', isDone ? 'text-green-700' : isRunning ? 'text-indigo-700' : 'text-gray-400'].join(' ')}>
                    {stage.label}
                    {isDone    && <span className="ml-2 font-normal text-green-500">완료</span>}
                    {isRunning && <span className="ml-2 font-normal text-indigo-400 animate-pulse">실행 중…</span>}
                  </p>
                  {(isRunning || isDone) && (
                    <p className="text-xs text-gray-400 mt-0.5">{detail ?? stage.desc}</p>
                  )}
                </div>
              </div>
            );
          })}

          {/* ── 시간 기반 후속 단계 (URL 완료 후 elapsed 기준) ── */}
          {POST_URL_STAGES.map((stage) => {
            const isDone    = urlDone && elapsed >= stage.to;
            const isRunning = urlDone && !isDone && elapsed >= stage.from;
            const isPending = !urlDone || elapsed < stage.from;

            return (
              <div key={stage.id} className="px-5 py-4 flex items-start gap-3">
                <div className="mt-0.5 shrink-0">
                  {isDone && (
                    <div className="w-5 h-5 rounded-full bg-green-500 flex items-center justify-center">
                      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7"/>
                      </svg>
                    </div>
                  )}
                  {isRunning && <div className="w-5 h-5 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin"/>}
                  {isPending && <div className="w-5 h-5 rounded-full border-2 border-gray-200"/>}
                </div>
                <div className="flex-1 min-w-0">
                  <p className={['text-sm font-semibold', isDone ? 'text-green-700' : isRunning ? 'text-indigo-700' : 'text-gray-400'].join(' ')}>
                    {stage.label}
                    {isDone    && <span className="ml-2 font-normal text-green-500">완료</span>}
                    {isRunning && <span className="ml-2 font-normal text-indigo-400 animate-pulse">실행 중…</span>}
                  </p>
                  {(isRunning || isDone) && (
                    <p className="text-xs text-gray-400 mt-0.5">{stage.desc}</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <p className="text-xs text-center text-gray-400 mt-5 leading-relaxed">
          분석 항목 수에 따라 최대 5분까지 소요될 수 있습니다.<br/>
          화면을 닫거나 새로고침하지 마세요.
        </p>
      </div>
    </div>
  );
}


/* ══════════════════════════════════════════════════════════ 공통 유틸 ══════ */

function isValidUrl(str) {
  try {
    const u = new URL(str);
    return u.protocol === 'https:' || u.protocol === 'http:';
  } catch {
    return false;
  }
}
