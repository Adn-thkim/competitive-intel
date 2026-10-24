/**
 * ComparisonMatrixReport
 * ----------------------
 * report_outputs["comparison_matrix"] envelope 렌더 (v0.12.2).
 * 설계: docs/design/comparison_matrix_node_design.md §3-2 content 구조.
 *
 * v0.12.2 UI 개선:
 *   1. 표 transpose — 행=분석 항목(feature), 열=상품(candidate). (feature 수 > candidate 수)
 *   2. feature 라벨 표시 (feature_id 대신 한국어 라벨). 모든 섹션 공통.
 *   3. 루브릭 점수 의미 설명 추가.
 *   4. Harvey Balls 해석 안내 + 글자(●○)·숫자 범례 정합.
 *   5. 하단 신뢰도·주의 섹션 명시적 그룹핑.
 *   v0.12.3 — 루브릭 박스 불릿화 + 상위 점수 부족분 안내 · Harvey 범례 제거 ·
 *             신뢰도 섹션 토글(기본 닫힘).
 *   v0.12.4 — 셀 핵심값 2줄 클램프 + 후버 툴팁(전체값·원문 근거·신뢰도) — 한눈에
 *             비교 가능하도록 부수 내용은 후버로 이연. 툴팁은 fixed 포지셔닝으로
 *             표 overflow 클리핑 회피.
 *   v0.12.5 — 행 정규화(comparison_normalizer) 연동: headline=display 핵심값 +
 *             condition(조건) 1줄 노출, 전체값(value)·조건·근거는 후버로.
 *   v0.12.6 — 조건 인라인 텍스트 제거 → Harvey Balls 글리프 흡수 아이콘(◑ 조건부 /
 *             ● 조건 없음)으로 대체. 조건 본문은 후버·title 에만.
 *   v0.12.7 — 나열형 feature(여행 혜택·앱 기능)는 items 를 줄바꿈 리스트로 셀에 표시.
 *             스칼라 feature 는 headline 단일값 유지. 조건은 후버.
 *   v0.12.8 — 셀의 명시/부분 배지 제거 · 조건부 기호(◑)는 조건 있을 때만 노출 ·
 *             표 우측 상단 조건부 범례 추가.
 *   v0.12.9 — 표 위 두괄식 요약 블록(KeyTakeaways): overall_comment + 핵심 선택 기준 +
 *             use_case(세그먼트)별 자사 우위/접전/열위. zone_summary×use_case_weights 클라이언트 조인.
 *   v0.12.10 — 완성도 점수 배지·루브릭 박스 웹 비노출 → 콘솔(console.info)로 이전.
 *   v0.12.11 — KeyTakeaways: use_case(세그먼트)별 자사 관점 한 줄 설명(summary) 렌더.
 */

import { useState, useEffect } from 'react';

const STATUS_META = {
  explicit:               { label: '명시',      cls: 'bg-green-100 text-green-700' },
  partial:                { label: '부분',      cls: 'bg-yellow-100 text-yellow-700' },
  inferred:               { label: '추론',      cls: 'bg-blue-100 text-blue-700' },
  unknown:                { label: '미확인',    cls: 'bg-gray-100 text-gray-500' },
  not_found:              { label: '미확인',    cls: 'bg-gray-100 text-gray-500' },
  requires_manual_check:  { label: '⚠ 검토',   cls: 'bg-red-100 text-red-700' },
};

// zone 판정·Harvey Balls 카드는 셀 색상 테두리로 흡수 → 웹 비노출(데이터는 KeyTakeaways·셀 테두리로 유지).
const SHOW_ZONE_CARD = false;
const SHOW_HARVEY_CARD = false;

const ZONE_META = {
  winning:  { title: 'Winning',  sub: '우위', bg: 'bg-green-50',  border: 'border-green-200',  text: 'text-green-800' },
  battling: { title: 'Battling', sub: '접전', bg: 'bg-amber-50',  border: 'border-amber-200',  text: 'text-amber-800' },
  losing:   { title: 'Losing',   sub: '열위', bg: 'bg-red-50',    border: 'border-red-200',    text: 'text-red-800' },
};

// Harvey Balls 5단계 — 0(없음)~4(완전). 채움이 많을수록 우수.
const HARVEY_GLYPHS = ['○', '◔', '◑', '◕', '●'];

// 루브릭 1~5점 기준 (docs/reference/report_taxonomy.md §2-1)
const RUBRIC_LEVELS = [
  { score: 1, text: '지원/미지원 같은 단순 이분(binary) 표기만 있음' },
  { score: 2, text: '정량 수치는 있으나 단위·기준 시점 누락' },
  { score: 3, text: '수치 + 단위 + 공식 출처 URL 을 갖춤' },
  { score: 4, text: '3점 요건 + 사용 시나리오(use case)별 가중치 반영' },
  { score: 5, text: '4점 요건 + 함정 항목(매수/매도 비대칭·한시 프로모션 등)을 각주로 명시' },
];
// score → 다음 점수를 받기 위해 보완해야 할 부분
const NEXT_LEVEL_HINT = {
  1: '정량 수치를 단위와 함께 기재하면 2점이 됩니다.',
  2: '각 수치에 단위와 공식 출처 URL 을 갖추면 3점이 됩니다.',
  3: '페르소나·사용 시나리오별 중요도(가중치)를 반영하면 4점이 됩니다.',
  4: '함정 항목(매수/매도 비대칭·한시 프로모션·ATM 실효성·통화 우대 차이)을 각주로 전면 명시하면 5점이 됩니다.',
};

// 완성도 점수·루브릭 설명을 콘솔에 출력 (웹 페이지 비노출 — v0.12.10).
function logRubric(score) {
  const lines = RUBRIC_LEVELS.map(
    lv => `  ${lv.score}점 = ${lv.text}${lv.score === score ? '  ← 현재' : ''}`);
  const hint = (score < 5 && NEXT_LEVEL_HINT[score])
    ? `\n  ↑ 상위 점수까지: ${NEXT_LEVEL_HINT[score]}` : '';
  console.info(
    `[비교 매트릭스] 완성도 ${score}/5  ${'★'.repeat(score)}${'☆'.repeat(5 - score)}\n` +
    '(상품 간 우열이 아니라 비교표 자체의 충실도 점수)\n' +
    lines.join('\n') + hint);
}

// 자사 zone → 색·라벨 (요약 블록용)
const ZONE_TAG = {
  win:  { t: '우위', c: 'bg-green-100 text-green-700' },
  bat:  { t: '접전', c: 'bg-amber-100 text-amber-700' },
  lose: { t: '열위', c: 'bg-red-100 text-red-700' },
};

/* 기준 칩 — feature 라벨 + 자사 zone 태그 */
function ZChip({ fid, labelOf, zoneOf }) {
  const m = ZONE_TAG[zoneOf[fid]];
  return (
    <span className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded bg-white border border-gray-200">
      <span className="text-gray-700">{labelOf[fid] || fid}</span>
      {m
        ? <span className={`px-1 rounded font-medium ${m.c}`}>{m.t}</span>
        : <span className="px-1 rounded bg-gray-100 text-gray-400">미확인</span>}
    </span>
  );
}

/* 요약 문장을 비교 축 단위(마침표)로 분리 — 한 줄에 한 축으로 가독성 확보.
 * 소수점(0.5~2.5%)은 마침표 뒤 공백이 없어 분리되지 않음. */
const splitAxes = (s) =>
  String(s || '').split(/(?<=\.)\s+/).map(x => x.trim()).filter(Boolean);

/* 두괄식 분석 요약 — 핵심 선택 기준 + 세그먼트(use_case)별 자사 포지션.
 * 기존 envelope(zone_summary × use_case_weights)를 클라이언트에서 결정론 조인. 백엔드 무변경. */
/* 제품 관점 요약 (v2.0) — 상품별 설계 · 자사 특징 · 공통점/차이점 · 조건부 강점/약점.
 * 활용유형(use_case) 분류는 positioning_map 으로 이관. */
function ProductSummary({ product, nameOf }) {
  const p          = product ?? {};
  const perProduct = p.per_product ?? [];
  const own        = p.own_characteristics ?? [];
  const common     = p.commonalities ?? [];
  const diff       = p.differences ?? [];
  const strengths  = p.strengths ?? [];
  const weaknesses = p.weaknesses ?? [];
  if (!perProduct.length && !own.length && !common.length && !diff.length &&
      !strengths.length && !weaknesses.length) return null;

  const Bullets = ({ items, mark }) => (
    <ul className="text-sm text-gray-800 space-y-1">
      {items.map((s, i) => (
        <li key={i} className="flex gap-1.5"><span className="shrink-0 text-gray-300">{mark}</span><span>{s}</span></li>
      ))}
    </ul>
  );
  const Tradeoffs = ({ items, mark, markColor }) => (
    <ul className="text-sm text-gray-800 space-y-1">
      {items.map((t, i) => (
        <li key={i} className="flex gap-1.5">
          <span className={`shrink-0 ${markColor}`}>{mark}</span>
          <span>
            {t.point}
            {t.condition && t.condition !== '무조건' && (
              <span className="ml-1 text-[11px] text-gray-500">— 조건: {t.condition}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );

  return (
    <div className="mb-4 rounded-xl border border-indigo-100 bg-indigo-50/40 p-3.5 space-y-3">
      <p className="text-xs font-bold text-indigo-700">📌 제품 관점 요약</p>

      {perProduct.length > 0 && (
        <div className="space-y-1">
          <p className="text-[11px] font-semibold text-gray-500">상품별 설계</p>
          <ul className="text-sm text-gray-800 space-y-1">
            {perProduct.map((it, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="shrink-0 font-medium text-gray-700">{nameOf?.[it.candidate_id] ?? it.candidate_id}:</span>
                <span>{it.design_summary}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {own.length > 0 && (
        <div className="space-y-1">
          <p className="text-[11px] font-semibold text-gray-500">자사 특징</p>
          <Bullets items={own} mark="·" />
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {common.length > 0 && (
          <div className="space-y-1">
            <p className="text-[11px] font-semibold text-gray-500">공통점</p>
            <Bullets items={common} mark="·" />
          </div>
        )}
        {diff.length > 0 && (
          <div className="space-y-1">
            <p className="text-[11px] font-semibold text-gray-500">차이점</p>
            <Bullets items={diff} mark="·" />
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {strengths.length > 0 && (
          <div className="space-y-1">
            <p className="text-[11px] font-semibold text-green-600">자사 강점 (조건부)</p>
            <Tradeoffs items={strengths} mark="＋" markColor="text-green-500" />
          </div>
        )}
        {weaknesses.length > 0 && (
          <div className="space-y-1">
            <p className="text-[11px] font-semibold text-red-600">자사 약점 (조건부)</p>
            <Tradeoffs items={weaknesses} mark="－" markColor="text-red-500" />
          </div>
        )}
      </div>

      <p className="text-[10px] text-gray-400">
        강점·약점은 사용 환경(결제액·빈도·통화 등)에 따라 유불리가 갈릴 수 있어 조건과 함께 표기됩니다.
      </p>
    </div>
  );
}

/* 셀 1개 — headline(핵심값)을 표에 노출, condition(조건)은 작은 1줄, 전체값·원문
 * 근거는 후버 툴팁으로 이연 (CM-D2). 툴팁은 position:fixed 로 overflow 클리핑 회피. */
function MatrixCell({ cell, ownZone }) {
  const [tip, setTip] = useState(null);   // {top,left} | null
  // 자사 셀 zone 강조 — 우위(녹)·열위(적) 색상 테두리(zone 판정 흡수). 접전·타사는 무테두리.
  const zoneRing = ownZone === 'win'  ? 'ring-2 ring-inset ring-green-400'
                 : ownZone === 'lose' ? 'ring-2 ring-inset ring-red-400'
                 : '';
  if (!cell) return <td className={`px-3 py-2 text-xs text-gray-300 border-b border-l border-gray-100 ${zoneRing}`}>·</td>;
  const meta = STATUS_META[cell.extraction_status] ?? STATUS_META.unknown;
  const isUnknown = cell.extraction_status === 'not_found' || cell.extraction_status === 'unknown';
  const condition = cell.condition || '';
  const items = cell.items || [];                                   // 나열형 feature 항목
  const fullValue = cell.value || cell.display || '';
  const valueDiffers = fullValue && fullValue !== cell.display;     // headline 정규화로 축약됨
  // 후버 상세가 의미 있는 경우: 조건·원문 근거가 있거나 전체값이 headline 과 다를 때
  const hasDetail = !isUnknown && (!!condition || !!cell.evidence || valueDiffers);

  const onEnter = (e) => {
    if (!hasDetail) return;
    const r = e.currentTarget.getBoundingClientRect();
    setTip({
      top:  r.bottom + 6,
      left: Math.max(8, Math.min(r.left, window.innerWidth - 320)),
    });
  };

  return (
    <td
      onMouseEnter={onEnter}
      onMouseLeave={() => setTip(null)}
      className={`px-3 py-2 align-top border-b border-l border-gray-100 ${zoneRing} ${cell.manual_check_required ? 'bg-red-50/50' : ''} ${hasDetail ? 'cursor-help' : ''}`}
    >
      <div className="flex items-start gap-1">
        <div className="flex-1 min-w-0">
          {items.length > 0 ? (
            /* 나열형 feature — 항목을 줄바꿈으로 구분 */
            <ul className="text-xs leading-relaxed text-gray-800 space-y-0.5">
              {items.map((it, idx) => (
                <li key={idx} className="flex gap-1">
                  <span className="shrink-0 text-gray-400">·</span>
                  <span className="truncate" title={it}>{it}</span>
                </li>
              ))}
              {cell.footnote_refs?.map(r => (
                <sup key={r} className="text-purple-600 font-semibold ml-0.5">[{r}]</sup>
              ))}
            </ul>
          ) : (
            /* 스칼라 feature — 핵심값(headline) */
            <div
              className={`text-xs font-medium leading-relaxed ${isUnknown ? 'text-gray-400 italic font-normal' : 'text-gray-800'} ${hasDetail ? 'underline decoration-dotted decoration-gray-300 underline-offset-2' : ''}`}
              style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
            >
              {cell.display || '미확인'}
              {cell.footnote_refs?.map(r => (
                <sup key={r} className="text-purple-600 font-semibold ml-0.5">[{r}]</sup>
              ))}
            </div>
          )}
        </div>
        {/* 조건부 기호 — 조건이 있을 때만 ◑ 노출. 조건 본문은 후버에. */}
        {!isUnknown && condition && (
          <span
            className="shrink-0 text-sm leading-none mt-0.5 text-amber-500"
            title={`조건부: ${condition}`}
          >
            ◑
          </span>
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        {cell.as_of && <span className="text-[10px] text-gray-400">기준 {cell.as_of}</span>}
        {cell.source_url && (
          <a href={cell.source_url} target="_blank" rel="noreferrer"
            className="text-[10px] text-blue-500 hover:underline" title={cell.source_url}>
            출처↗
          </a>
        )}
      </div>

      {tip && (
        <div
          style={{ position: 'fixed', top: tip.top, left: tip.left, zIndex: 50, width: 300 }}
          className="rounded-lg bg-gray-900 text-white shadow-xl p-3 pointer-events-none"
        >
          {cell.display && <p className="text-xs font-semibold leading-relaxed mb-1">{cell.display}</p>}
          {valueDiffers && (
            <p className="text-[11px] text-gray-200 leading-relaxed">{fullValue}</p>
          )}
          {condition && (
            <p className="text-[11px] text-amber-200 mt-1.5 leading-relaxed">조건 · {condition}</p>
          )}
          {cell.evidence && (
            <p className="text-[11px] text-gray-300 italic border-l-2 border-gray-600 pl-2 mt-1.5 leading-relaxed">
              “{cell.evidence}”
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-gray-400">
            <span>근거 등급 {meta.label}</span>
            {cell.confidence > 0 && <span>신뢰도 {Math.round(cell.confidence * 100)}%</span>}
            {cell.as_of && <span>기준 {cell.as_of}</span>}
          </div>
        </div>
      )}
    </td>
  );
}

export default function ComparisonMatrixReport({ report }) {
  const [showReliability, setShowReliability] = useState(false);   // 기본 닫힘 (item 6)
  if (!report) return null;
  const content   = report.content ?? {};
  const table     = content.feature_table ?? { columns: [], rows: [] };
  // v2.0 — 상단 요약을 product_summary(제품 관점)로 대체. zone/use_case/harvey 폐기.
  // (구 필드는 envelope 에 더 이상 없어 아래 폴백은 항상 빈 값 → 관련 렌더 블록 비노출.)
  const product   = content.product_summary ?? {};
  const zone      = content.zone_summary ?? {};
  const weights   = content.use_case_weights ?? [];
  const harvey    = content.harvey_balls ?? [];
  const promos    = content.promotional_footnotes ?? [];
  const traps     = content.traps_footnote ?? [];
  const warnings  = report.warnings ?? [];
  const degraded  = warnings.some(w => String(w).includes('degrade'));

  // 행=feature, 열=candidate 로 transpose (item 1)
  const features   = table.columns;          // 행
  const candidates = table.rows;             // 열 (own 첫 번째 — 노드에서 정렬됨)
  const cellOf = (cid, fid) =>
    candidates.find(c => c.candidate_id === cid)?.cells?.[fid];

  // 자사 셀 zone 강조용 맵 — feature_id → 'win'|'lose'(접전은 미표기). 자사 컬럼에만 테두리.
  const ownId = candidates.find(c => c.is_own)?.candidate_id;
  const ownZoneOf = {};
  (zone.winning ?? []).forEach(z => { ownZoneOf[z.feature_id] = 'win'; });
  (zone.losing  ?? []).forEach(z => { ownZoneOf[z.feature_id] = 'lose'; });

  // 라벨 맵 (item 2) — 모든 섹션에서 feature_id 대신 한국어 라벨 사용
  const labelOf = Object.fromEntries(features.map(c => [c.feature_id, c.label]));
  const nameOf  = Object.fromEntries(candidates.map(r => [r.candidate_id, r.candidate_name || r.candidate_id]));
  const scoreRationale = warnings.find(w => String(w).startsWith('score_rationale:'));
  const otherWarnings  = warnings.filter(w => w !== scoreRationale && !String(w).includes('degrade'));

  // 완성도 점수·루브릭은 웹에 표시하지 않고 콘솔에만 출력
  const score = report.evaluation_score ?? 0;
  useEffect(() => { logRubric(score); }, [score]);

  return (
    <div className="mb-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
      {/* ── 1. 헤더 ── */}
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <h3 className="text-lg font-bold text-gray-900">📊 {content.title ?? '비교 매트릭스'}</h3>
        {degraded && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 font-medium">
            degraded — LLM 판정 생략 (표만 제공)
          </span>
        )}
      </div>
      <p className="text-xs text-gray-400 mb-2">
        {report.rubric_version} · 생성 {String(report.generated_at ?? '').slice(0, 19).replace('T', ' ')}
      </p>
      {/* ── 두괄식 분석 요약 (표 위 최상단) — v2.0 제품 관점 요약 ── */}
      <ProductSummary product={product} nameOf={nameOf} />

      {/* ── 2. feature_table (transpose: 행=항목, 열=상품) ── */}
      {/* 범례 (표 우측 상단) — 조건부 기호 */}
      <div className="flex flex-wrap justify-end items-center gap-x-3 gap-y-1 mb-1">
        <span className="inline-flex items-center gap-1 text-[11px] text-gray-500">
          <span className="text-amber-500">◑</span>
          조건부 — 일정 조건 충족 시 적용(자세한 내용은 해당 셀에 마우스를 올려 확인)
        </span>
      </div>
      <div className="overflow-x-auto rounded-xl border border-gray-200 mb-5">
        <table className="min-w-full text-left border-collapse">
          <thead>
            <tr className="bg-gray-50">
              <th className="px-3 py-2 text-xs font-semibold text-gray-500 sticky left-0 bg-gray-50 z-10 min-w-[130px] max-w-[180px]">
                분석 항목
              </th>
              {candidates.map(c => (
                <th key={c.candidate_id} className={`px-3 py-2 text-xs font-semibold min-w-[180px] border-l border-gray-200 ${c.is_own ? 'text-blue-700 bg-blue-50/60' : 'text-gray-600'}`}>
                  {c.candidate_name || c.candidate_id}
                  {c.is_own && <span className="block text-[10px] font-medium text-blue-500">자사</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {features.map(f => (
              <tr key={f.feature_id} className="hover:bg-gray-50/50">
                <td className="px-3 py-2 align-top border-b border-gray-100 sticky left-0 bg-white z-10 min-w-[130px] max-w-[180px]">
                  <span className="text-xs font-semibold text-gray-800 break-keep">{f.label}</span>
                  {f.category && <span className="block text-[10px] text-gray-400">{f.category}</span>}
                </td>
                {candidates.map(c => (
                  <MatrixCell
                    key={c.candidate_id}
                    cell={cellOf(c.candidate_id, f.feature_id)}
                    ownZone={c.candidate_id === ownId ? ownZoneOf[f.feature_id] : undefined}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── 3. zone_summary (셀 색상 테두리로 흡수 — 카드 비노출) ── */}
      {SHOW_ZONE_CARD && (zone.winning?.length || zone.battling?.length || zone.losing?.length) ? (
        <div className="mb-5">
          <h4 className="text-sm font-bold text-gray-800 mb-1">
            Zone 판정 (자사 관점)
          </h4>
          <p className="text-xs text-gray-600 mb-2">자사가 각 항목에서 우위(Winning)·접전(Battling)·열위(Losing) 중 어디에 있는지 분류한 결과입니다.</p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {Object.entries(ZONE_META).map(([key, meta]) => (
              <div key={key} className={`rounded-xl border p-3 ${meta.bg} ${meta.border}`}>
                <p className={`text-xs font-bold mb-2 ${meta.text}`}>
                  {meta.title} <span className="font-normal">({meta.sub} {zone[key]?.length ?? 0})</span>
                </p>
                {(zone[key] ?? []).map((z, i) => (
                  <div key={i} className="mb-2 last:mb-0">
                    <span className="text-[11px] font-semibold text-gray-700">{labelOf[z.feature_id] ?? z.feature_id}</span>
                    <p className="text-xs text-gray-600 mt-0.5">{z.rationale}</p>
                  </div>
                ))}
              </div>
            ))}
          </div>
          {zone.overall_comment && (
            <p className="mt-3 text-sm text-gray-700 bg-gray-50 rounded-lg p-3 border border-gray-100">
              {zone.overall_comment}
            </p>
          )}
        </div>
      ) : null}

      {/* ── 4. harvey_balls (셀 색상 테두리로 흡수 — 카드 비노출) ── */}
      {SHOW_HARVEY_CARD && harvey.length > 0 && (
        <div className="mb-5">
          <h4 className="text-sm font-bold text-gray-800 mb-1">
            정성 평가 (Harvey Balls)
          </h4>
          <p className="text-xs text-gray-600 mb-2">
            수치로 비교하기 어려운 항목을 5단계로 표현합니다. <b>채움이 많을수록(● 쪽) 우수</b>합니다.
            <span className="ml-2 text-gray-500">
              척도: {HARVEY_GLYPHS.map((g, i) => <span key={i} className="ml-1">{g} {i}</span>)}
            </span>
          </p>
          <div className="space-y-2">
            {harvey.map((h, i) => (
              <div key={i} className="rounded-lg border border-gray-100 p-2.5">
                <p className="text-xs font-semibold text-gray-700 mb-1">{labelOf[h.feature_id] ?? h.feature_id}</p>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-1">
                  {Object.entries(h.ratings ?? {}).map(([cid, r]) => (
                    <span key={cid} className="flex items-center gap-1.5 text-xs">
                      <span className="text-lg text-indigo-600 leading-none">{HARVEY_GLYPHS[r] ?? '·'}</span>
                      <span className="text-gray-600">{nameOf[cid] ?? cid}</span>
                    </span>
                  ))}
                </div>
                {h.interpretation && (
                  <p className="text-xs text-gray-600 bg-gray-50 rounded px-2 py-1.5">{h.interpretation}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 5. 신뢰도·주의 섹션 (토글, 기본 닫힘 — item 6) ── */}
      {(promos.length > 0 || traps.length > 0 || warnings.length > 0) && (
        <div className="pt-4 border-t border-gray-100">
          <button
            type="button"
            onClick={() => setShowReliability(v => !v)}
            className="w-full flex items-center justify-between text-left group"
          >
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider group-hover:text-gray-700">
              리포트 신뢰도·주의 사항
              <span className="ml-2 normal-case font-normal text-gray-400">
                ({promos.length + traps.length + warnings.length}건)
              </span>
            </span>
            <span className="text-gray-400 text-xs">{showReliability ? '▲ 접기' : '▼ 펼치기'}</span>
          </button>

          {showReliability && (<>
          <p className="text-xs text-gray-400 mt-2 mb-2">
            비교 결과를 해석할 때 함께 고려해야 할 단서입니다 — 기간 한정 혜택, 데이터 점검 필요 항목, 평가 근거.
          </p>

          {promos.length > 0 && (
            <div className="mb-2">
              <p className="text-[11px] font-semibold text-purple-700 mb-0.5">기간 한정 혜택 (영구 강점과 구분 필요)</p>
              {promos.map(f => (
                <p key={f.ref} className="text-xs text-purple-700">
                  <sup className="font-semibold">[{f.ref}]</sup>{' '}
                  {nameOf[f.candidate_id] ?? f.candidate_id} · {labelOf[f.feature_id] ?? f.feature_id}
                  {f.valid_until ? ` — ~${f.valid_until} 까지` : ''}
                </p>
              ))}
            </div>
          )}

          {traps.length > 0 && (
            <div className="mb-2">
              <p className="text-[11px] font-semibold text-amber-700 mb-0.5">데이터 점검 필요 (값의 범위·시점 불확실)</p>
              {traps.map((t, i) => {
                // "AP-N 후보: comp_x × feat_y — 설명" 에서 feat_id 를 라벨로 치환
                const pretty = String(t).replace(/feat_[0-9a-z_]+/g, m => labelOf[m] ?? m)
                                        .replace(/(own_|comp_)[0-9A-Za-z가-힣_]+/g, m => nameOf[m] ?? m);
                return <p key={`t${i}`} className="text-xs text-amber-700">⚑ {pretty}</p>;
              })}
            </div>
          )}

          {(scoreRationale || otherWarnings.length > 0) && (
            <div>
              <p className="text-[11px] font-semibold text-gray-500 mb-0.5">평가 메모</p>
              {scoreRationale && (
                <p className="text-xs text-gray-500">{String(scoreRationale).replace('score_rationale:', '점수 근거:')}</p>
              )}
              {otherWarnings.map((w, i) => (
                <p key={`w${i}`} className="text-xs text-gray-400">· {w}</p>
              ))}
            </div>
          )}
          </>)}
        </div>
      )}
    </div>
  );
}
