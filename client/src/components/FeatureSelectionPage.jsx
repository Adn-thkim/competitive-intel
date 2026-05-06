import { useState } from 'react';

/* ── 우선순위 배지 ─────────────────────────────────────────────────────────── */

function PriorityBadge({ priority }) {
  const map = {
    high:   { label: '높음', cls: 'bg-red-100 text-red-700' },
    medium: { label: '중간', cls: 'bg-yellow-100 text-yellow-700' },
    low:    { label: '낮음', cls: 'bg-gray-100 text-gray-500' },
  };
  const { label, cls } = map[priority] ?? { label: priority, cls: 'bg-gray-100 text-gray-500' };
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${cls}`}>
      {label}
    </span>
  );
}

/* ── 커버리지 요약 칩 ─────────────────────────────────────────────────────── */

function CoverageSummary({ summary }) {
  const { sufficient = 0, partial = 0, not_found = 0 } = summary ?? {};
  const total = sufficient + partial + not_found;
  if (total === 0) return null;

  return (
    <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
      <span className="text-xs text-gray-400">URL 커버리지:</span>
      {sufficient > 0 && (
        <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-green-100 text-green-700">
          충분 {sufficient}
        </span>
      )}
      {partial > 0 && (
        <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-700">
          부분 {partial}
        </span>
      )}
      {not_found > 0 && (
        <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-red-100 text-red-600">
          미확보 {not_found}
        </span>
      )}
    </div>
  );
}

/* ── 개별 Feature 카드 ────────────────────────────────────────────────────── */

function FeatureCard({ feature, isSelected, onToggle }) {
  return (
    <label
      className={[
        'flex items-start gap-3 p-3.5 rounded-lg border-2 cursor-pointer transition-colors',
        isSelected
          ? 'border-indigo-500 bg-indigo-50'
          : 'border-gray-200 bg-white hover:border-indigo-300',
      ].join(' ')}
    >
      <input
        type="checkbox"
        className="mt-0.5 w-4 h-4 accent-indigo-600 shrink-0"
        checked={isSelected}
        onChange={onToggle}
      />
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2 mb-0.5">
          <span className="text-sm font-semibold text-gray-900">
            {feature.feature_name}
          </span>
          <PriorityBadge priority={feature.priority} />
        </div>
        <p className="text-xs text-gray-500 leading-relaxed">
          {feature.description}
        </p>
        <CoverageSummary summary={feature.coverage_summary} />
      </div>
    </label>
  );
}

/* ── Purpose 섹션 ─────────────────────────────────────────────────────────── */

function PurposeSection({ purpose, selectedIds, onToggleFeature }) {
  const featureIds = purpose.features.map(f => f.feature_id);
  const allSelected = featureIds.every(id => selectedIds.has(id));
  const someSelected = featureIds.some(id => selectedIds.has(id));
  const selectedCount = featureIds.filter(id => selectedIds.has(id)).length;

  function handleToggleAll() {
    if (allSelected) {
      // 전체 해제
      featureIds.forEach(id => {
        if (selectedIds.has(id)) onToggleFeature(id);
      });
    } else {
      // 전체 선택
      featureIds.forEach(id => {
        if (!selectedIds.has(id)) onToggleFeature(id);
      });
    }
  }

  return (
    <div className="mb-7">
      {/* 섹션 헤더 */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-base font-bold text-gray-800">
            {purpose.purpose_label}
          </h3>
          <p className="text-xs text-gray-400 mt-0.5">
            {selectedCount} / {featureIds.length}개 선택됨
          </p>
        </div>
        <button
          type="button"
          onClick={handleToggleAll}
          className={[
            'text-xs font-medium px-3 py-1.5 rounded-lg border transition-colors',
            allSelected
              ? 'bg-indigo-50 border-indigo-300 text-indigo-700 hover:bg-indigo-100'
              : someSelected
                ? 'bg-gray-50 border-gray-300 text-gray-600 hover:bg-gray-100'
                : 'bg-gray-50 border-gray-300 text-gray-600 hover:bg-gray-100',
          ].join(' ')}
        >
          {allSelected ? '전체 해제' : '전체 선택'}
        </button>
      </div>

      {/* Feature 카드 목록 */}
      <div className="space-y-2">
        {purpose.features.map(feature => (
          <FeatureCard
            key={feature.feature_id}
            feature={feature}
            isSelected={selectedIds.has(feature.feature_id)}
            onToggle={() => onToggleFeature(feature.feature_id)}
          />
        ))}
      </div>
    </div>
  );
}

/* ── 메인 컴포넌트 ────────────────────────────────────────────────────────── */

/**
 * FeatureSelectionPage
 *
 * Props
 * -----
 * - intakeResult : /api/approve 응답
 *   interrupt_value = {
 *     type: "feature_selection",
 *     purposes: [
 *       {
 *         purpose_id: string,
 *         purpose_label: string,
 *         features: [{ feature_id, feature_name, description, priority, coverage_summary }]
 *       }
 *     ]
 *   }
 * - threadId  : LangGraph thread_id
 * - onApproved: (data) => void
 * - onReset   : () => void
 */
export default function FeatureSelectionPage({ intakeResult, threadId, onApproved, onReset }) {
  const iv       = intakeResult?.interrupt_value ?? {};
  const purposes = iv.purposes ?? [];

  // 초기 선택: 모든 feature를 기본 선택 상태로 시작
  const allFeatureIds = purposes.flatMap(p => p.features.map(f => f.feature_id));
  const [selectedIds, setSelectedIds] = useState(new Set(allFeatureIds));

  const [submitting, setSubmitting] = useState(false);
  const [error, setError]           = useState('');

  const totalFeatures = allFeatureIds.length;
  const selectedCount = selectedIds.size;

  /* ── 개별 토글 ─────────────────────────────────────────────────────────── */
  function toggleFeature(featureId) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(featureId)) {
        next.delete(featureId);
      } else {
        next.add(featureId);
      }
      return next;
    });
    setError('');
  }

  /* ── 선택된 purpose 역산 ───────────────────────────────────────────────── */
  function deriveSelectedPurposes() {
    const purposeSet = new Set();
    for (const purpose of purposes) {
      const hasSelected = purpose.features.some(f => selectedIds.has(f.feature_id));
      if (hasSelected) purposeSet.add(purpose.purpose_id);
    }
    return [...purposeSet];
  }

  /* ── 제출 ──────────────────────────────────────────────────────────────── */
  async function handleSubmit() {
    if (selectedCount < 1) {
      setError('최소 1개 이상의 분석 항목을 선택해야 합니다.');
      return;
    }

    setSubmitting(true);
    setError('');

    try {
      const res = await fetch('/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          thread_id: threadId,
          resume: {
            selected_purposes:    deriveSelectedPurposes(),
            selected_feature_ids: [...selectedIds],
          },
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
    }
  }

  /* ── 전체 선택 / 전체 해제 ─────────────────────────────────────────────── */
  const allSelected = selectedCount === totalFeatures;

  function toggleAll() {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(allFeatureIds));
    }
    setError('');
  }

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-2xl mx-auto">

        {/* 타이틀 */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">분석 항목 선택</h1>
          <p className="text-sm text-gray-500 mt-1">
            이번 분석에 포함할 비교 항목을 선택하세요.
            목적(Purpose) 단위로 전체 선택하거나, 항목별로 세부 조정할 수 있습니다.
          </p>
        </div>

        {/* 선택 현황 패널 */}
        <div className="bg-white rounded-xl border border-gray-200 p-4 mb-6 shadow-sm">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-700">선택 현황</span>
            <div className="flex items-center gap-3">
              <span className="text-sm font-semibold text-indigo-600">
                {selectedCount} / {totalFeatures}개 선택
              </span>
              <button
                type="button"
                onClick={toggleAll}
                className="text-xs font-medium px-2.5 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
              >
                {allSelected ? '전체 해제' : '전체 선택'}
              </button>
            </div>
          </div>
          <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-indigo-500 transition-all duration-300"
              style={{ width: totalFeatures > 0 ? `${(selectedCount / totalFeatures) * 100}%` : '0%' }}
            />
          </div>
          {selectedCount === 0 && (
            <p className="text-xs text-red-500 mt-1.5">
              최소 1개 이상의 항목을 선택해야 합니다.
            </p>
          )}
        </div>

        {/* Purpose 섹션 목록 */}
        {purposes.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <p className="text-sm">분석 항목이 없습니다.</p>
          </div>
        ) : (
          purposes.map((purpose, idx) => (
            <div key={purpose.purpose_id}>
              {/* 섹션 구분선 (첫 번째 제외) */}
              {idx > 0 && (
                <div className="flex items-center gap-2 mb-6 -mt-1">
                  <div className="flex-1 h-px bg-gray-200" />
                </div>
              )}
              <PurposeSection
                purpose={purpose}
                selectedIds={selectedIds}
                onToggleFeature={toggleFeature}
              />
            </div>
          ))
        )}

        {/* 오류 메시지 */}
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {error}
          </div>
        )}

        {/* 버튼 영역 */}
        <div className="flex gap-3 pt-2 pb-8">
          <button
            onClick={onReset}
            disabled={submitting}
            className="px-4 py-2 text-sm font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            처음으로
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || selectedCount < 1}
            className="flex-1 py-2.5 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting
              ? '분석 요청 중…'
              : `선택 완료 (${selectedCount}개) → 다음 단계`}
          </button>
        </div>

      </div>
    </div>
  );
}
