import { useState } from 'react';
import ReactionSourcesCard from './ReactionSourcesCard.jsx';

/* ── 안내 박스 (v0.10.18a — source_flow 별 색상 분기) ────────────────────── */

const INTRO_BOX_CLASS = {
  A:     'bg-gray-50 border-gray-200 text-gray-700',                     // 흐름 A — 중성
  'A+B': 'bg-amber-50 border-amber-200 text-amber-900',                   // 흐름 A+B — 호박색 (다른 리포트 인용 강조)
  B:     'bg-blue-50 border-blue-200 text-blue-900',                      // B-only — 파란색 (URL 수집 부재 명시)
};

// 2026-11 — 단계 카드의 진행 시점 배지
const PHASE_BADGE = {
  done:  { label: '완료',         cls: 'bg-gray-100 text-gray-500' },
  after: { label: '선택 후 실행', cls: 'bg-indigo-100 text-indigo-700' },
};

function IntroBox({ sourceFlow, text, intro }) {
  const cls = INTRO_BOX_CLASS[sourceFlow] ?? INTRO_BOX_CLASS.A;

  // 2026-11 — 구조화 안내(한 줄 요약 + 단계 목록)가 있으면 우선 렌더
  if (intro?.steps?.length) {
    return (
      <div className={`rounded-lg border px-3.5 py-3 mb-3 ${cls}`}>
        <p className="text-sm font-semibold mb-2.5">{intro.summary}</p>
        <ol className="space-y-2">
          {intro.steps.map((step, i) => {
            const badge = PHASE_BADGE[step.phase] ?? PHASE_BADGE.after;
            return (
              <li key={i} className="flex items-start gap-2.5">
                <span className="mt-0.5 w-5 h-5 shrink-0 rounded-full bg-white border border-gray-300 text-[11px] font-semibold flex items-center justify-center">
                  {i + 1}
                </span>
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold">{step.title}</span>
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${badge.cls}`}>
                      {badge.label}
                    </span>
                  </div>
                  <p className="text-xs opacity-80 leading-relaxed mt-0.5">{step.detail}</p>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    );
  }

  if (!text) return null;
  return (
    <div className={`rounded-lg border px-3.5 py-2.5 mb-3 text-xs leading-relaxed whitespace-pre-line ${cls}`}>
      {text}
    </div>
  );
}

/* ── 비교 매트릭스 feature 표 (2026-11 — feature × 상품, 셀 클릭 시 URL 목록) ──── */

// URL 확보 판정 = feature_mapping 단계 LLM 의 사전 예측 (실제 값 추출 전)
const COV_META = {
  sufficient: { label: '확보', dot: 'bg-emerald-500 border-emerald-500' },
  partial:    { label: '일부', dot: 'bg-amber-200 border-amber-500' },
  not_found:  { label: '없음', dot: 'bg-white border-gray-300' },
};

function CovDot({ cov }) {
  const meta = COV_META[cov] ?? COV_META.not_found;
  return <span className={`inline-block w-2.5 h-2.5 rounded-full border-2 align-[-1px] ${meta.dot}`} />;
}

const shortUrl = (url) =>
  (url || '').replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/$/, '');

// candidate 별 표시 URL — 기존 URL + 접속 확인된 추가 URL(보조). 접속 실패 URL 은 숨김.
function urlsOf(cd) {
  if (!cd) return [];
  return [
    ...(cd.existing_urls ?? []).map(u => ({ url: u.url, extra: false })),
    ...(cd.additional_urls ?? []).filter(u => u.validated).map(u => ({ url: u.url, extra: true })),
  ];
}

function FeatureMatrix({ report, selectedKeys, onToggleFeature }) {
  const rt = report.report_type;
  const [openId, setOpenId] = useState(null);

  // 열 = 상품. 서버 candidates(자사 → 경쟁사 ID 순, 비교 매트릭스 리포트와 동일) 우선.
  // 없으면(구 서버 payload) coverage_details 에서 수집 — own_/comp_ 접두사 제거, 같은 순서 규칙.
  const cols = report.candidates?.length
    ? report.candidates
    : [...new Set(report.features.flatMap(f => (f.coverage_details ?? []).map(cd => cd.candidate_id)))]
        .sort((a, b) => (b.startsWith('own_') - a.startsWith('own_')) || a.localeCompare(b))
        .map(id => ({
          candidate_id:   id,
          label:          id.replace(/^(own|comp|func)_/, ''),
          candidate_type: id.startsWith('own_') ? 'own' : 'competitor',
        }));
  const hasCore = report.features.some(f => f.priority === 'high');
  // 2026-11 — 표시 순서만 핵심(★) 항목 우선 (안정 정렬). 선택 키·서버 전송 순서는 report.features 원순서 유지.
  const rows = [...report.features].sort(
    (a, b) => (b.priority === 'high') - (a.priority === 'high'));

  return (
    <>
      {/* 범례 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500 mb-1.5 px-1">
        <span className="font-medium text-gray-600">URL 확보 판정</span>
        {Object.entries(COV_META).map(([k, m]) => (
          <span key={k}><CovDot cov={k} /> {m.label}</span>
        ))}
        <span className="text-gray-400">· 숫자 = 찾은 URL 수 · 행을 누르면 URL 목록</span>
        {hasCore && <span className="ml-auto"><span className="text-amber-500">★</span> 핵심 항목</span>}
      </div>

      {/* 좁은 화면에서는 상품 열이 잘리지 않도록 표를 가로 스크롤 */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
        <table className="w-full min-w-[560px] table-fixed text-sm">
          <colgroup>
            <col className="w-10" />
            <col />
            {cols.map(c => <col key={c.candidate_id} className="w-[88px]" />)}
            <col className="w-7" />
          </colgroup>
          <thead className="bg-gray-50 text-[11px] text-gray-500">
            <tr>
              <th />
              <th className="text-left font-medium py-2">feature</th>
              {cols.map(c => (
                <th
                  key={c.candidate_id}
                  title={c.label}
                  style={{ overflowWrap: 'anywhere' }}
                  className={`px-1 py-2 font-medium leading-tight break-keep ${c.candidate_type === 'own' ? 'text-indigo-600' : ''}`}
                >
                  {c.label}
                </th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map(f => {
              const selected = selectedKeys.has(fkey(rt, f.feature_id));
              const byCand = Object.fromEntries((f.coverage_details ?? []).map(cd => [cd.candidate_id, cd]));
              const isOpen = openId === f.feature_id;
              return [
                <tr
                  key={f.feature_id}
                  onClick={() => setOpenId(isOpen ? null : f.feature_id)}
                  className={`border-t border-gray-100 cursor-pointer hover:bg-gray-50 ${isOpen ? 'bg-indigo-50/60' : ''} ${selected ? '' : 'opacity-50'}`}
                >
                  <td className="pl-3 py-2 align-top" onClick={e => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      className="mt-0.5 w-4 h-4 accent-indigo-600"
                      checked={selected}
                      onChange={() => onToggleFeature(rt, f.feature_id)}
                      aria-label={f.feature_name}
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <div className="font-medium text-gray-900 text-[13px]">
                      {f.priority === 'high' && <span className="text-amber-500 mr-0.5">★</span>}
                      {f.feature_name}
                    </div>
                    {f.description && (
                      <div className="text-[11px] text-gray-400 leading-snug mt-0.5">
                        {f.description}
                      </div>
                    )}
                  </td>
                  {cols.map(c => {
                    const cd = byCand[c.candidate_id];
                    return (
                      <td key={c.candidate_id} className="text-center py-2 text-xs">
                        <CovDot cov={cd?.coverage} /> <span className="text-gray-500">{urlsOf(cd).length}</span>
                      </td>
                    );
                  })}
                  <td className="text-center text-[10px] text-gray-400 pr-1">{isOpen ? '▲' : '▼'}</td>
                </tr>,
                isOpen && (
                  <tr key={`${f.feature_id}-urls`} className="bg-indigo-50/40">
                    <td />
                    <td colSpan={cols.length + 2} className="py-2 pr-3">
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 text-[11px]">
                        {cols.map(c => {
                          const cd = byCand[c.candidate_id];
                          const urls = urlsOf(cd);
                          const meta = COV_META[cd?.coverage] ?? COV_META.not_found;
                          return (
                            <div key={c.candidate_id} className="min-w-0">
                              <div className="font-medium text-gray-700">
                                {c.label} <CovDot cov={cd?.coverage} /> <span className="text-gray-400 font-normal">{meta.label}</span>
                              </div>
                              {urls.length === 0
                                ? <div className="text-gray-400 italic">찾은 URL 없음</div>
                                : urls.map((u, i) => (
                                    <div key={i} className="flex items-center gap-1 min-w-0">
                                      <a
                                        href={u.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        title={u.url}
                                        className="text-indigo-600 hover:underline truncate"
                                      >
                                        {shortUrl(u.url)}
                                      </a>
                                      {u.extra && <span className="shrink-0 text-[10px] text-gray-500 bg-gray-100 px-1 rounded">보조</span>}
                                    </div>
                                  ))}
                            </div>
                          );
                        })}
                      </div>
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-gray-400 mt-1.5 px-1">
        판정은 찾은 URL 을 보고 내린 <b>사전 예측</b>이며, 실제 값 추출은 선택 완료 후 진행됩니다.
      </p>
    </>
  );
}

/* ── Report 섹션 (v0.10.18a — source_flow 별 UI 분기 + B-only 카드 결합) ──── */

// 선택 단위 복합키 — 같은 feature_id가 여러 리포트에 공유돼도 리포트별로 독립 토글되도록 한다.
const fkey = (reportType, id) => `${reportType}::${id}`;

// 2026-11 — feature_selection 화면에 노출하는 리포트 (현재 생성 중인 리포트만).
const VISIBLE_REPORT_TYPES = new Set(['comparison_matrix', 'reaction_insight']);

// 2026-11 — reaction_insight 는 feature 선택 단위가 없으므로 리포트 자체를 1개 선택 단위로 다룬다.
// (서버로는 selected_purposes 포함 여부로만 전달 — selected_feature_ids 에는 들어가지 않음)
const REPORT_UNIT_ID = '__report__';
const selectionIds = (report) =>
  report.report_type === 'reaction_insight'
    ? [REPORT_UNIT_ID]
    : report.features.map(f => f.feature_id);

// 2026-11 — 리포트 단위 토글 스위치. on=전체 선택, partial=일부 선택, off=미선택.
// 클릭: on → 전체 해제, 그 외 → 전체 선택.
const TOGGLE_META = {
  on:      { text: '켜짐', track: 'bg-indigo-600', knob: 'translate-x-5' },
  partial: { text: '일부', track: 'bg-indigo-300', knob: 'translate-x-2.5' },
  off:     { text: '꺼짐', track: 'bg-gray-300',   knob: 'translate-x-0' },
};

function ReportToggle({ state, onToggle, label }) {
  const meta = TOGGLE_META[state];
  return (
    <button
      type="button"
      role="switch"
      aria-checked={state === 'on' ? 'true' : state === 'partial' ? 'mixed' : 'false'}
      aria-label={`${label} 생성 여부`}
      onClick={onToggle}
      className="flex items-center gap-2 shrink-0"
    >
      <span className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${meta.track}`}>
        <span className={`inline-block h-5 w-5 ml-0.5 rounded-full bg-white shadow transition-transform ${meta.knob}`} />
      </span>
      <span className="text-xs font-medium text-gray-600 w-6 text-left">{meta.text}</span>
    </button>
  );
}

function ReportSection({ report, selectedKeys, onToggleFeature }) {
  // v0.10.18a — server 가 전달하는 신설 메타데이터 (후방 호환: source_flow 누락 시 "A")
  const sourceFlow         = report.source_flow ?? 'A';
  const introText          = report.intro_text  ?? '';

  // 2026-11 — reaction_insight: 리포트 단위 체크박스 + 수집 현황·공식 YouTube 채널 카드 표시
  const isReactionInsight    = report.report_type === 'reaction_insight';
  const ownedYoutubeCard     = report.owned_youtube_card;
  const urlPreview           = report.url_preview;
  // v0.12.1 — 모든 리포트 카드의 feature 를 사용자가 선택/해제 가능하게 함.
  // (이전: B-only·marketing_social 은 자동 포함·체크박스 비활성.)
  const checkboxDisabled     = false;

  const featureIds = selectionIds(report);
  const rt = report.report_type;
  const allSelected  = featureIds.length > 0 && featureIds.every(id => selectedKeys.has(fkey(rt, id)));
  const someSelected = featureIds.some(id => selectedKeys.has(fkey(rt, id)));
  const selectedCount = featureIds.filter(id => selectedKeys.has(fkey(rt, id))).length;

  function handleToggleAll() {
    if (checkboxDisabled) return;   // B-only · marketing_social — 자동 포함, 전체 선택/해제 비활성
    if (allSelected) {
      featureIds.forEach(id => {
        if (selectedKeys.has(fkey(rt, id))) onToggleFeature(rt, id);
      });
    } else {
      featureIds.forEach(id => {
        if (!selectedKeys.has(fkey(rt, id))) onToggleFeature(rt, id);
      });
    }
  }

  return (
    <div className="mb-7">
      {/* 섹션 헤더 — 2026-11: 리포트 단위 선택을 두 리포트 모두 우측 토글 스위치로 통일 */}
      <div className={`flex items-center justify-between ${isReactionInsight ? 'mb-3' : 'mb-1.5'}`}>
        <div>
          <h3 className="text-base font-bold text-gray-800">
            {report.report_label}
          </h3>
          {!isReactionInsight && (
            <p className="text-xs text-gray-500 mt-0.5">
              <b className="text-indigo-600">{selectedCount}</b> / {featureIds.length}개 feature 선택
            </p>
          )}
        </div>
        {!checkboxDisabled && (
          <ReportToggle
            state={allSelected ? 'on' : someSelected ? 'partial' : 'off'}
            onToggle={handleToggleAll}
            label={report.report_label}
          />
        )}
      </div>

      {/* 2026-11 — 구 '선택 현황' 바를 feature 선택 수 막대로 흡수 (비교 매트릭스 전용) */}
      {!isReactionInsight && (
        <div className="h-1 bg-gray-200 rounded-full mb-3 overflow-hidden">
          <div
            className="h-full bg-indigo-500 rounded-full transition-all duration-300"
            style={{ width: featureIds.length ? `${(selectedCount / featureIds.length) * 100}%` : '0%' }}
          />
        </div>
      )}

      {/* v0.10.18a — 안내 박스 (source_flow 별 색상 분기) */}
      <IntroBox sourceFlow={sourceFlow} text={introText} intro={report.intro_steps} />

      {/* 2026-11 — reaction_insight: 안내 박스 아래 수집 대상(수집 현황 + 제외 공식 채널 통합) */}
      {isReactionInsight ? (
        <ReactionSourcesCard preview={urlPreview} youtubeCard={ownedYoutubeCard} />
      ) : (
        /* 2026-11 — feature 카드 목록 → feature × 상품 표 */
        <FeatureMatrix report={report} selectedKeys={selectedKeys} onToggleFeature={onToggleFeature} />
      )}
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
export default function FeatureSelectionPage({ intakeResult, threadId, onApproved, onReset, onStarted, onFailed }) {
  const iv      = intakeResult?.interrupt_value ?? {};
  // 2026-11 — 생성 중인 리포트(비교 매트릭스·고객 반응 인사이트)만 노출·선택 대상으로 한다.
  // 숨긴 리포트는 selected_purposes·selected_feature_ids 에 들어가지 않는다.
  const reports = (iv.reports ?? []).filter(r => VISIBLE_REPORT_TYPES.has(r.report_type));

  // 초기 선택: 모든 선택 단위(feature, reaction_insight 는 리포트 자체)를 (report,id) 복합키로 기본 선택.
  // 복합키라 같은 feature_id가 여러 리포트에 있어도 카드별로 독립 토글된다.
  const allKeys = reports.flatMap(r =>
    selectionIds(r).map(id => fkey(r.report_type, id)));
  const [selectedKeys, setSelectedKeys] = useState(new Set(allKeys));

  const [submitting, setSubmitting] = useState(false);
  const [error, setError]           = useState('');

  const selectedCount = selectedKeys.size;

  /* ── 개별 토글 ─────────────────────────────────────────────────────────── */
  function toggleFeature(reportType, id) {
    const k = fkey(reportType, id);
    setSelectedKeys(prev => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
    setError('');
  }

  /* ── 선택된 report_type 역산 (v0.10 키 정합) ──────────────────────────── */
  // server 측 호환을 위해 키 이름은 selected_purposes 유지(state.py 코멘트), 값은 선택된 report_type 목록.
  function deriveSelectedReportTypes() {
    const reportTypeSet = new Set();
    for (const report of reports) {
      // reaction_insight 는 리포트 단위 체크박스(REPORT_UNIT_ID) 선택 여부로 포함된다.
      const ids = selectionIds(report);
      if (ids.some(id => selectedKeys.has(fkey(report.report_type, id)))) {
        reportTypeSet.add(report.report_type);
      }
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
    // v0.12.4 점진 렌더링 — 분석 시작을 App 에 알려 state 폴링을 가동한다.
    // (graph 실행 중 체크포인트에서 report_outputs 가 보이는 즉시 결과 페이지로 전환)
    onStarted?.();

    // reaction_insight 포함 여부는 selected_purposes(deriveSelectedReportTypes)로 전달.
    // 2026-11 — 순서 고정: 선택 집합(Set)의 삽입 순서(=클릭 이력)가 아니라 **서버가 준 feature 순서**로
    // 보낸다. comparison_matrix 는 selected_feature_ids 순서로 표·정규화 입력을 만들고 그 해시가 캐시 키라,
    // 체크 해제→재체크만으로 순서가 바뀌면 같은 선택인데도 캐시가 미스된다(재현: 재체크 항목이 맨 뒤로 이동).
    const selectedFeatureIds = [...new Set(
      reports.flatMap(r => r.features
        .filter(f => selectedKeys.has(fkey(r.report_type, f.feature_id)))
        .map(f => f.feature_id))
    )];

    try {
      const res = await fetch('/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          thread_id: threadId,
          // 리포트 단계는 백그라운드 실행 + 즉시 ack — App 의 /api/state 폴링이 완료/오류 처리.
          background: true,
          resume: {
            // v0.10: 키 이름 selected_purposes 유지(server state.py 호환), 값은 report_type 목록
            selected_purposes:    deriveSelectedReportTypes(),
            selected_feature_ids: selectedFeatureIds,
          },
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data?.error ?? `서버 오류 (HTTP ${res.status})`);
      }

      // 백그라운드 ack(status:"running")면 폴링이 완료/오류를 처리한다.
      // submitting 을 유지해 로딩 상태를 보존하고, 페이지 전환은 App 이 수행한다.
      if (data.status === 'running') return;
      onApproved(data);   // 동기로 끝난 경우(하위호환)
    } catch (err) {
      setError(err.message ?? '알 수 없는 오류가 발생했습니다.');
      onFailed?.();   // 폴링 중지 (분석 시작 실패)
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-4xl mx-auto">

        {/* 타이틀 */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">분석 항목 선택</h1>
          <p className="text-sm text-gray-500 mt-1">
            생성할 리포트를 켜고, 비교 매트릭스는 비교할 feature 를 고르세요.
          </p>
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
                selectedKeys={selectedKeys}
                onToggleFeature={toggleFeature}
              />
            </div>
          ))
        )}

        {/* 2026-11 — 구 선택 현황 패널의 '선택 없음' 안내 이전 (제출 버튼 비활성 사유) */}
        {selectedCount === 0 && (
          <p className="mb-2 text-xs text-red-500">
            생성할 리포트를 켜거나 비교할 feature 를 1개 이상 선택해야 합니다.
          </p>
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
              : '선택 완료 → 다음 단계'}
          </button>
        </div>

      </div>
    </div>
  );
}
