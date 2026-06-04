import { useState } from 'react';
import OwnedChannelCard from './OwnedChannelCard.jsx';

/* ── 안내 박스 (v0.10.18a — source_flow 별 색상 분기) ────────────────────── */

const INTRO_BOX_CLASS = {
  A:     'bg-gray-50 border-gray-200 text-gray-700',                     // 흐름 A — 중성
  'A+B': 'bg-amber-50 border-amber-200 text-amber-900',                   // 흐름 A+B — 호박색 (다른 리포트 인용 강조)
  B:     'bg-blue-50 border-blue-200 text-blue-900',                      // B-only — 파란색 (URL 수집 부재 명시)
};

function IntroBox({ sourceFlow, text }) {
  if (!text) return null;
  const cls = INTRO_BOX_CLASS[sourceFlow] ?? INTRO_BOX_CLASS.A;
  return (
    <div className={`rounded-lg border px-3.5 py-2.5 mb-3 text-xs leading-relaxed ${cls}`}>
      {text}
    </div>
  );
}

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

/* ── Coverage 상세 (v0.10.16 — candidate × coverage × URL) ───────────────── */

const COVERAGE_META = {
  sufficient: { label: '충분',   chipCls: 'bg-green-100 text-green-700' },
  partial:    { label: '부분',   chipCls: 'bg-yellow-100 text-yellow-700' },
  not_found:  { label: '미확보', chipCls: 'bg-red-100 text-red-600' },
};

/* v0.10.28a (D46) — origin × source 메타 별 chip 분기.
 * macro 는 source_tier 메타가 official_statistics / news_supplement 로 추가 분기.
 * 그 외 5종 origin 은 단일 라벨.
 */
function originChip(u) {
  const origin = u.origin;
  if (origin === 'macro_search') {
    if (u.source_tier === 'official_statistics') {
      return { label: '공식 통계', cls: 'text-purple-700 bg-purple-50' };
    }
    if (u.source_tier === 'news_supplement') {
      return { label: '뉴스 보강', cls: 'text-orange-700 bg-orange-50' };
    }
    return { label: '매크로', cls: 'text-purple-600 bg-purple-50' };
  }
  if (origin === 'official_source')      return { label: '공식',        cls: 'text-gray-700 bg-gray-100' };
  if (origin === 'official_subpage')     return { label: '공식 sub-page', cls: 'text-gray-700 bg-gray-100' };
  if (origin === 'blog_community')       return { label: '블로그·커뮤니티', cls: 'text-teal-700 bg-teal-50' };
  if (origin === 'youtube_reactions')    return { label: 'YouTube',      cls: 'text-red-700 bg-red-50' };
  if (origin === 'owned_channel_search') return { label: '운영 채널',     cls: 'text-pink-700 bg-pink-50' };
  if (origin === 'brave_search')         return { label: 'Brave',        cls: 'text-blue-600 bg-blue-50' };
  // unknown origin — 회색 fallback
  return { label: origin || '미상', cls: 'text-gray-500 bg-gray-100' };
}

function CoverageDetails({ details }) {
  if (!details || details.length === 0) {
    return (
      <p className="text-xs text-gray-400 italic mt-2">
        candidate별 coverage 상세 없음.
      </p>
    );
  }

  return (
    <div className="mt-3 space-y-3 border-t border-gray-200 pt-3">
      {details.map((cov, idx) => {
        const meta = COVERAGE_META[cov.coverage] ?? {
          label: cov.coverage, chipCls: 'bg-gray-100 text-gray-500',
        };
        const existing  = cov.existing_urls   ?? [];
        const additional = cov.additional_urls ?? [];
        // v0.10.28a (D47 c) — candidate_label 우선 표시. server 가 macro 등 특수 라벨 부착.
        // 미부착 시 candidate_id fallback.
        const candidateDisplay = cov.candidate_label || cov.candidate_id || '(unknown)';
        // macro candidate 는 자사·경쟁사와 시각적 구분 (font-mono → font-sans)
        const candidateCls = cov.candidate_id === 'macro'
          ? 'font-sans font-medium text-purple-700 truncate'
          : 'font-mono text-gray-700 truncate';
        return (
          <div key={`${cov.candidate_id}-${idx}`} className="text-xs">
            {/* candidate 헤더 */}
            <div className="flex items-center gap-2 mb-1.5">
              <span className={candidateCls}>
                {candidateDisplay}
              </span>
              <span className={`px-1.5 py-0.5 rounded font-medium ${meta.chipCls}`}>
                {meta.label}
              </span>
            </div>

            {/* 기존 URL 목록 */}
            <div className="ml-2 mb-1">
              <span className="text-gray-500">기존 URL ({existing.length}건)</span>
              {existing.length === 0 ? null : (
                <ul className="mt-0.5 ml-2 space-y-0.5">
                  {existing.map((u, ui) => (
                    <li key={ui} className="flex items-center gap-1.5 text-gray-600 truncate">
                      <span className="text-gray-400 shrink-0">•</span>
                      <a
                        href={u.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-600 hover:underline truncate"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {u.url}
                      </a>
                      {(() => {
                        // v0.10.28a (D46) — origin × source_tier 별 6종 분기.
                        const chip = originChip(u);
                        return (
                          <span className={`text-[10px] px-1 rounded shrink-0 ${chip.cls}`}>
                            {chip.label}
                          </span>
                        );
                      })()}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* 추가 URL 목록 */}
            <div className="ml-2">
              <span className="text-gray-500">추가 URL ({additional.length}건)</span>
              {additional.length === 0 ? null : (
                <ul className="mt-0.5 ml-2 space-y-0.5">
                  {additional.map((u, ui) => (
                    <li key={ui} className="flex items-center gap-1.5 text-gray-600 truncate">
                      <span className="text-gray-400 shrink-0">•</span>
                      <a
                        href={u.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-600 hover:underline truncate"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {u.url}
                      </a>
                      {u.validated ? (
                        <span className="text-[10px] text-green-700 bg-green-50 px-1 rounded shrink-0">
                          ✓ {u.http_status ?? 'OK'}
                        </span>
                      ) : (
                        <span className="text-[10px] text-red-600 bg-red-50 px-1 rounded shrink-0">
                          ✗ {u.http_status ?? 'fail'}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── 개별 Feature 카드 (v0.10.18a — source_flow 별 URL 영역 조건부 렌더) ──── */

function FeatureCard({
  feature,
  isSelected,
  onToggle,
  urlCoverageVisible = true,   // v0.10.18a — B-only 시 false
  checkboxDisabled   = false,  // v0.10.18a — B-only 시 자동 선택 + 비활성
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = urlCoverageVisible
    && Array.isArray(feature.coverage_details)
    && feature.coverage_details.length > 0;

  return (
    <div
      className={[
        'rounded-lg border-2 transition-colors',
        checkboxDisabled
          ? 'border-blue-200 bg-blue-50/30'                              // v0.10.18a — B-only 카드: 옅은 파란색
          : isSelected
            ? 'border-indigo-500 bg-indigo-50'
            : 'border-gray-200 bg-white hover:border-indigo-300',
      ].join(' ')}
    >
      <label
        className={[
          'flex items-start gap-3 p-3.5',
          checkboxDisabled ? 'cursor-default' : 'cursor-pointer',
        ].join(' ')}
      >
        <input
          type="checkbox"
          className={[
            'mt-0.5 w-4 h-4 accent-indigo-600 shrink-0',
            checkboxDisabled ? 'opacity-60 cursor-not-allowed' : '',
          ].join(' ')}
          checked={isSelected}
          onChange={checkboxDisabled ? undefined : onToggle}
          disabled={checkboxDisabled}
        />
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold text-gray-900">
              {feature.feature_name}
            </span>
            <PriorityBadge priority={feature.priority} />
            {checkboxDisabled && (
              <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 shrink-0">
                자동 포함
              </span>
            )}
            {hasDetails && (
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setExpanded(v => !v);
                }}
                className="ml-auto text-[11px] font-medium text-indigo-600 hover:text-indigo-800 hover:underline shrink-0"
              >
                {expanded ? '▲ 상세 닫기' : '▼ URL 상세 보기'}
              </button>
            )}
          </div>
          {feature.description && (
            <p className="text-xs text-gray-500 leading-relaxed">
              {feature.description}
            </p>
          )}
          {urlCoverageVisible
            ? <CoverageSummary summary={feature.coverage_summary} />
            : (
              <p className="text-[11px] text-blue-700/80 italic mt-1.5">
                자동 도출 항목 — 다른 리포트 결과로부터 생성됩니다 (URL 수집 없음)
              </p>
            )
          }
        </div>
      </label>

      {/* v0.10.16 — 확장 시 candidate × coverage × URL 상세 (B-only 시 미렌더) */}
      {expanded && hasDetails && (
        <div className="px-3.5 pb-3.5">
          <CoverageDetails details={feature.coverage_details} />
        </div>
      )}
    </div>
  );
}

/* ── Report 섹션 (v0.10.18a — source_flow 별 UI 분기 + B-only 카드 결합) ──── */

function ReportSection({ report, selectedIds, onToggleFeature }) {
  // v0.10.18a — server 가 전달하는 신설 메타데이터 (후방 호환: source_flow 누락 시 "A")
  const sourceFlow         = report.source_flow ?? 'A';
  const introText          = report.intro_text  ?? '';
  const urlCoverageVisible = report.url_coverage_visible !== false;  // 기본 true
  const isBOnly            = sourceFlow === 'B';

  // v0.10.28b D45 a — marketing_social 카드는 B-only 형식 + 별도 owned_channels_card 렌더링
  const isMarketingSocial    = report.report_type === 'marketing_social';
  const ownedChannelsCard    = report.owned_channels_card;
  const checkboxDisabled     = isBOnly || isMarketingSocial;

  const featureIds   = report.features.map(f => f.feature_id);
  const allSelected  = featureIds.every(id => selectedIds.has(id));
  const someSelected = featureIds.some(id => selectedIds.has(id));
  const selectedCount = featureIds.filter(id => selectedIds.has(id)).length;

  function handleToggleAll() {
    if (checkboxDisabled) return;   // B-only · marketing_social — 자동 포함, 전체 선택/해제 비활성
    if (allSelected) {
      featureIds.forEach(id => {
        if (selectedIds.has(id)) onToggleFeature(id);
      });
    } else {
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
            {report.report_label}
          </h3>
          <p className="text-xs text-gray-400 mt-0.5">
            {checkboxDisabled
              ? `${featureIds.length}개 자동 포함`
              : `${selectedCount} / ${featureIds.length}개 선택됨`}
          </p>
        </div>
        {!checkboxDisabled && (
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
        )}
      </div>

      {/* v0.10.18a — 안내 박스 (source_flow 별 색상 분기) */}
      <IntroBox sourceFlow={sourceFlow} text={introText} />

      {/* v0.10.28b D45 a — marketing_social 의 별도 "공식 채널" 카드 (안내 박스 아래) */}
      {isMarketingSocial && ownedChannelsCard ? (
        <OwnedChannelCard card={ownedChannelsCard} />
      ) : null}

      {/* Feature 카드 목록 */}
      <div className="space-y-2">
        {report.features.map(feature => (
          <FeatureCard
            key={feature.feature_id}
            feature={feature}
            isSelected={selectedIds.has(feature.feature_id)}
            onToggle={() => onToggleFeature(feature.feature_id)}
            urlCoverageVisible={urlCoverageVisible}
            checkboxDisabled={checkboxDisabled}
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
 *   v0.10.18a interrupt_value = {
 *     type: "feature_selection",
 *     reports: [
 *       {
 *         report_type:          string,                // D4 enum 7종 중 하나
 *         report_label:         string,                // 한국어 레이블
 *         source_flow:          "A" | "B" | "A+B",    // v0.10.18a 신설 (UI 분기 키)
 *         intro_text:           string,                // v0.10.18a 신설 (안내 박스 문구)
 *         url_coverage_visible: boolean,               // v0.10.18a 신설 (B-only 시 false)
 *         features: [{
 *           feature_id, feature_name, description, priority,
 *           // 흐름 A·A+B (url_coverage_visible=true): coverage_summary + coverage_details 보유
 *           // B-only      (url_coverage_visible=false): coverage_summary = null, coverage_details = null
 *           coverage_summary, coverage_details
 *         }]
 *       }
 *     ]
 *   }
 *   ※ v0.10 에서 purposes/purpose_id/purpose_label → reports/report_type/report_label 로 변경됨.
 *     resume payload 의 selected_purposes 키는 server 호환을 위해 그대로 유지하지만,
 *     값으로는 선택된 report_type 목록을 전달한다(state.py 코멘트 참고).
 *   ※ v0.10.18a — B-only 리포트(positioning_map · executive_summary)는 자동 포함되어
 *     사용자 선택 대상이 아니나, selected_feature_ids 에는 포함하여 server 가 후속 노드 처리
 *     영역을 동일하게 인식하도록 한다.
 * - threadId  : LangGraph thread_id
 * - onApproved: (data) => void
 * - onReset   : () => void
 */
export default function FeatureSelectionPage({ intakeResult, threadId, onApproved, onReset }) {
  const iv      = intakeResult?.interrupt_value ?? {};
  const reports = iv.reports ?? [];

  // 초기 선택: 모든 feature를 기본 선택 상태로 시작
  const allFeatureIds = reports.flatMap(r => r.features.map(f => f.feature_id));
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

  /* ── 선택된 report_type 역산 (v0.10 키 정합) ──────────────────────────── */
  // server 측 호환을 위해 키 이름은 selected_purposes 유지(state.py 코멘트), 값은 선택된 report_type 목록.
  function deriveSelectedReportTypes() {
    const reportTypeSet = new Set();
    for (const report of reports) {
      const hasSelected = report.features.some(f => selectedIds.has(f.feature_id));
      if (hasSelected) reportTypeSet.add(report.report_type);
    }
    return [...reportTypeSet];
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
            // v0.10: 키 이름 selected_purposes 유지(server state.py 호환), 값은 report_type 목록
            selected_purposes:    deriveSelectedReportTypes(),
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

        {/* Report 섹션 목록 (v0.10 키 정합 — purpose → report) */}
        {reports.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <p className="text-sm">분석 항목이 없습니다.</p>
          </div>
        ) : (
          reports.map((report, idx) => (
            <div key={report.report_type}>
              {/* 섹션 구분선 (첫 번째 제외) */}
              {idx > 0 && (
                <div className="flex items-center gap-2 mb-6 -mt-1">
                  <div className="flex-1 h-px bg-gray-200" />
                </div>
              )}
              <ReportSection
                report={report}
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
