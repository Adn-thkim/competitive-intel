import { useState, useEffect, useRef } from 'react';

const MIN_SELECT = 1;
const MAX_SELECT = 10;

/* ─────────────────────────── 파이프라인 진행 상태 패널 ── */

/**
 * OfficialSourceResolver + url_retry_node 실행 중 단계별 진행 상황을 표시한다.
 *
 * 4개 단계:
 *   url_discovery        → URL 탐색 중     (LLM, candidate별 병렬)
 *   url_validation       → URL 검증 중     (HTTP 병렬)
 *   url_retry_llm        → 실패 URL 재탐색 중 (LLM)
 *   url_retry_validation → 재탐색 URL 검증 중 (HTTP)
 */

const PIPELINE_STAGES = [
  {
    id:      'url_discovery',
    label:   'URL 탐색',
    desc:    'AI가 각 경쟁사의 공식 URL 후보를 수집합니다.',
  },
  {
    id:      'url_validation',
    label:   'URL 검증',
    desc:    '수집된 URL의 실제 접근 가능 여부를 확인합니다.',
  },
  {
    id:      'url_retry_llm',
    label:   '실패 URL 재탐색',
    desc:    '검증에 실패한 URL을 AI가 새로 탐색합니다.',
  },
  {
    id:      'url_retry_validation',
    label:   '재탐색 URL 검증',
    desc:    '재탐색된 URL의 접근 가능 여부를 다시 확인합니다.',
  },
];

function PipelineProgressPanel({ progress, failedCount }) {
  const currentIdx = PIPELINE_STAGES.findIndex(s => s.id === progress?.stage) ?? 0;
  const safeIdx    = currentIdx < 0 ? 0 : currentIdx;

  return (
    <div className="mb-4 p-4 bg-indigo-50 border border-indigo-200 rounded-xl">
      {/* 헤더 */}
      <div className="flex items-center gap-2 mb-3">
        <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin shrink-0" />
        <span className="text-sm font-semibold text-indigo-800">
          {progress?.message ?? 'URL 탐색 중…'}
        </span>
        {progress?.detail && (
          <span className="ml-auto text-xs text-indigo-500 shrink-0">{progress.detail}</span>
        )}
      </div>

      {/* 단계 목록 */}
      <ol className="space-y-1.5">
        {PIPELINE_STAGES.map((s, idx) => {
          const isDone    = idx < safeIdx;
          const isActive  = idx === safeIdx;
          const isPending = idx > safeIdx;

          return (
            <li key={s.id} className="flex items-start gap-2.5">
              {/* 아이콘 */}
              <span className="mt-0.5 shrink-0">
                {isDone && (
                  <svg className="w-4 h-4 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                )}
                {isActive && (
                  <span className="block w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                )}
                {isPending && (
                  <span className="block w-4 h-4 rounded-full border-2 border-gray-300" />
                )}
              </span>

              {/* 텍스트 */}
              <div className="min-w-0">
                <span className={[
                  'text-xs font-medium',
                  isDone    ? 'text-green-700' :
                  isActive  ? 'text-indigo-700' :
                              'text-gray-400',
                ].join(' ')}>
                  {s.label}
                </span>
                {isActive && (
                  <p className="text-xs text-indigo-500 mt-0.5">
                    {s.id === 'url_retry_llm' && failedCount != null && failedCount > 0
                      ? `검증 실패 ${failedCount}건 — ${s.desc}`
                      : s.desc}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** confidence → 색상 클래스 */
function confidenceClass(score) {
  if (score >= 0.8) return 'bg-green-100 text-green-800';
  if (score >= 0.55) return 'bg-yellow-100 text-yellow-800';
  return 'bg-red-100 text-red-700';
}

/** competition_type → 한국어 레이블 */
function typeLabel(type) {
  return { direct: '직접', indirect: '간접', substitute: '대체재' }[type] ?? type;
}

/** competition_type → 배지 색상 */
function typeClass(type) {
  return (
    { direct: 'bg-blue-100 text-blue-800', indirect: 'bg-purple-100 text-purple-800', substitute: 'bg-orange-100 text-orange-800' }[type] ?? 'bg-gray-100 text-gray-700'
  );
}

/* ─────────────────────────────────────────────────────── 개별 카드 ── */

function CandidateCard({ item, isSelected, onToggle, disabled }) {
  const conf = item.confidence ?? 0;
  return (
    <label
      className={[
        'flex items-start gap-3 p-4 rounded-lg border-2 cursor-pointer transition-colors',
        isSelected
          ? 'border-indigo-500 bg-indigo-50'
          : 'border-gray-200 bg-white hover:border-indigo-300',
        disabled && !isSelected ? 'opacity-40 cursor-not-allowed' : '',
      ].join(' ')}
    >
      <input
        type="checkbox"
        className="mt-1 w-4 h-4 accent-indigo-600 shrink-0"
        checked={isSelected}
        onChange={onToggle}
        disabled={disabled && !isSelected}
      />
      <div className="flex-1 min-w-0">
        {/* 헤더 */}
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <span className="font-semibold text-gray-900 text-sm">
            {item.brand ?? ''}{item.brand && item.product_name ? ' · ' : ''}{item.product_name ?? item.method_name}
          </span>
          {item.competition_type && (
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${typeClass(item.competition_type)}`}>
              {typeLabel(item.competition_type)}
            </span>
          )}
          {item.provider_type && (
            <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-teal-100 text-teal-800">
              {item.provider_type}
            </span>
          )}
          <span className={`ml-auto text-xs font-medium px-2 py-0.5 rounded-full ${confidenceClass(conf)}`}>
            적합도 {Math.round(conf * 100)}%
          </span>
        </div>

        {/* 이유 목록 */}
        <ul className="text-xs text-gray-600 space-y-0.5 mt-1 list-disc list-inside">
          {(item.why_competitor ?? item.why_alternative ?? []).slice(0, 2).map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>

        {/* evidence (브랜드 후보만) */}
        {item.evidence_summary && (
          <p className="text-xs text-gray-500 mt-1 italic">{item.evidence_summary}</p>
        )}
      </div>
    </label>
  );
}

/* ─────────────────────────────────────────────────────── 섹션 헤더 ── */

function SectionHeader({ title, subtitle, count, selected }) {
  return (
    <div className="mb-3">
      <h3 className="text-base font-semibold text-gray-800">{title}</h3>
      <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>
      <span className="text-xs text-indigo-600 font-medium">{selected}개 선택됨 (이 섹션: {count}개)</span>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────── 메인 ── */

/**
 * CompetitorSelectionPage
 *
 * Props
 * -----
 * - intakeResult: /api/intake 또는 /api/approve의 두 번째 응답
 *   interrupt_value = { type:"competitor_selection", competitor_candidates:[...], functional_competitors:[...] }
 * - threadId     : LangGraph thread_id (재개 시 필요)
 * - onApproved   : (data) => void  ─ 선택 완료 후 호출
 * - onReset      : () => void      ─ 처음부터 다시 시작
 */
export default function CompetitorSelectionPage({ intakeResult, threadId, onApproved, onReset }) {
  const iv = intakeResult?.interrupt_value ?? {};
  const candidates  = iv.competitor_candidates  ?? [];
  const functional  = iv.functional_competitors ?? [];

  const [selected, setSelected]     = useState(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError]           = useState('');
  // 진행 상황 폴링
  // step: 'resolving' | 'retrying' | null
  const [progressStep, setProgressStep]   = useState(null);
  const [progressFailed, setProgressFailed] = useState(null); // 실패 건수
  const pollRef = useRef(null);

  const totalSelected = selected.size;
  const atMax = totalSelected >= MAX_SELECT;

  // ── 진행 상황 폴링 (/api/progress 실시간 단계) ──────────────────────────────
  useEffect(() => {
    if (!submitting || !threadId) {
      setProgressStep(null);
      setProgressFailed(null);
      return;
    }

    // 제출 직후 기본값 — 첫 폴링 전까지 표시
    setProgressStep({ stage: 'url_discovery', message: 'URL 탐색 중', detail: '' });
    setProgressFailed(null);

    let cancelled = false;

    async function poll() {
      if (cancelled) return;
      try {
        const res = await fetch(`/api/progress/${threadId}`);
        if (!res.ok || cancelled) return;
        const data = await res.json();

        if (data.progress && !cancelled) {
          setProgressStep(data.progress);
          // url_retry 단계에서 실패 건수를 detail에서 파싱
          if (data.progress.stage === 'url_retry_llm') {
            const m = data.progress.detail?.match(/^(\d+)개/);
            setProgressFailed(m ? parseInt(m[1], 10) : null);
          }
        }
      } catch {
        /* 폴링 오류는 무시 — 메인 요청 오류 처리와 분리 */
      }
    }

    poll();
    pollRef.current = setInterval(poll, 1500);

    return () => {
      cancelled = true;
      clearInterval(pollRef.current);
    };
  }, [submitting, threadId]);

  function toggle(id) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        if (next.size >= MAX_SELECT) return prev;   // 최대 초과 방지
        next.add(id);
      }
      return next;
    });
    setError('');
  }

  async function handleSubmit() {
    if (totalSelected < MIN_SELECT) {
      setError(`최소 ${MIN_SELECT}개 이상 선택해야 합니다.`);
      return;
    }

    setSubmitting(true);
    setError('');
    setProgressStep('resolving');
    setProgressFailed(null);

    try {
      const res = await fetch('/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          thread_id: threadId,
          resume:    { selected_ids: [...selected] },
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data?.error ?? `서버 오류 (HTTP ${res.status})`);
      }

      onApproved(data);
    } catch (err) {
      setError(err.message ?? '알 수 없는 오류가 발생했습니다.');
    } finally {
      setSubmitting(false);
      setProgressStep(null);
      setProgressFailed(null);
      clearInterval(pollRef.current);
    }
  }

  /* ── 선택 현황 바 ── */
  const progressPct = Math.min((totalSelected / MAX_SELECT) * 100, 100);

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-2xl mx-auto">

        {/* 타이틀 */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">경쟁사 선택</h1>
          <p className="text-sm text-gray-500 mt-1">
            분석할 경쟁 상품을 최소 {MIN_SELECT}개, 최대 {MAX_SELECT}개 선택하세요.
            브랜드 경쟁사와 기능적 대안을 합산해 {MAX_SELECT}개 한도가 적용됩니다.
          </p>
        </div>

        {/* 선택 현황 진행 바 */}
        <div className="bg-white rounded-xl border border-gray-200 p-4 mb-6 shadow-sm">
          <div className="flex justify-between text-sm mb-2">
            <span className="font-medium text-gray-700">선택 현황</span>
            <span className={`font-semibold ${atMax ? 'text-orange-600' : 'text-indigo-600'}`}>
              {totalSelected} / {MAX_SELECT}개
            </span>
          </div>
          <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300 ${atMax ? 'bg-orange-500' : 'bg-indigo-500'}`}
              style={{ width: `${progressPct}%` }}
            />
          </div>
          {atMax && (
            <p className="text-xs text-orange-600 mt-1.5">최대 선택 수에 도달했습니다. 기존 항목을 해제해야 추가 선택이 가능합니다.</p>
          )}
        </div>

        {/* ── 섹션 1: 브랜드 경쟁사 ── */}
        {candidates.length > 0 && (
          <div className="mb-8">
            <SectionHeader
              title="브랜드 경쟁 상품"
              subtitle="직접·간접·대체재 유형의 브랜드 상품 경쟁사 후보 (적합도 높은 순)"
              count={candidates.length}
              selected={[...selected].filter(id => !id.startsWith('func_')).length}
            />
            <div className="space-y-2">
              {candidates.map(c => (
                <CandidateCard
                  key={c.candidate_id}
                  item={c}
                  isSelected={selected.has(c.candidate_id)}
                  onToggle={() => toggle(c.candidate_id)}
                  disabled={atMax}
                />
              ))}
            </div>
          </div>
        )}

        {/* ── 섹션 2: 기능적 대안 ── */}
        {functional.length > 0 && (
          <div className="mb-8">
            <div className="flex items-center gap-2 mb-3">
              <div className="flex-1 h-px bg-gray-200" />
              <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">전통적·기능적 대안 수단</span>
              <div className="flex-1 h-px bg-gray-200" />
            </div>
            <SectionHeader
              title="기능적 대안 수단"
              subtitle="브랜드 없이 동일한 문제를 해결하는 전통적 방법 (선택 한도 공유)"
              count={functional.length}
              selected={[...selected].filter(id => id.startsWith('func_')).length}
            />
            <div className="space-y-2">
              {functional.map(f => (
                <CandidateCard
                  key={f.candidate_id}
                  item={f}
                  isSelected={selected.has(f.candidate_id)}
                  onToggle={() => toggle(f.candidate_id)}
                  disabled={atMax}
                />
              ))}
            </div>
          </div>
        )}

        {/* 진행 상황 패널 — 4단계 실시간 표시 */}
        {submitting && progressStep && (
          <PipelineProgressPanel
            progress={progressStep}
            failedCount={progressFailed}
          />
        )}

        {/* 오류 메시지 */}
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {error}
          </div>
        )}

        {/* 버튼 영역 */}
        <div className="flex gap-3 pt-2">
          <button
            onClick={onReset}
            disabled={submitting}
            className="px-4 py-2 text-sm font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            처음으로
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || totalSelected < MIN_SELECT}
            className="flex-1 py-2.5 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting
              ? '분석 요청 중…'
              : `선택 완료 (${totalSelected}개) → 분석 시작`}
          </button>
        </div>

      </div>
    </div>
  );
}
