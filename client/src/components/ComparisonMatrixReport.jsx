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
 */

import { useState } from 'react';

const STATUS_META = {
  explicit:               { label: '명시',      cls: 'bg-green-100 text-green-700' },
  partial:                { label: '부분',      cls: 'bg-yellow-100 text-yellow-700' },
  inferred:               { label: '추론',      cls: 'bg-blue-100 text-blue-700' },
  unknown:                { label: '미확인',    cls: 'bg-gray-100 text-gray-500' },
  not_found:              { label: '미확인',    cls: 'bg-gray-100 text-gray-500' },
  requires_manual_check:  { label: '⚠ 검토',   cls: 'bg-red-100 text-red-700' },
};

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

function ScoreBadge({ score }) {
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-indigo-100 text-indigo-800 text-sm font-semibold"
      title="비교표 자체의 완성도 점수 (데이터 우열이 아님)">
      완성도 {score}/5
      <span className="text-indigo-400 font-normal">{'★'.repeat(score)}{'☆'.repeat(5 - score)}</span>
    </span>
  );
}

/* 셀 1개 — transpose 후에도 동일 표기 규칙(CM-D2) */
function MatrixCell({ cell }) {
  if (!cell) return <td className="px-3 py-2 text-xs text-gray-300 border-b border-l border-gray-100">·</td>;
  const meta = STATUS_META[cell.extraction_status] ?? STATUS_META.unknown;
  const isUnknown = cell.extraction_status === 'not_found' || cell.extraction_status === 'unknown';
  return (
    <td className={`px-3 py-2 align-top border-b border-l border-gray-100 ${cell.manual_check_required ? 'bg-red-50/50' : ''}`}>
      <div className={`text-xs leading-relaxed ${isUnknown ? 'text-gray-400 italic' : 'text-gray-800'}`}>
        {cell.display || '미확인'}
        {cell.footnote_refs?.map(r => (
          <sup key={r} className="text-purple-600 font-semibold ml-0.5">[{r}]</sup>
        ))}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${meta.cls}`}>{meta.label}</span>
        {cell.as_of && <span className="text-[10px] text-gray-400">기준 {cell.as_of}</span>}
        {cell.source_url && (
          <a href={cell.source_url} target="_blank" rel="noreferrer"
            className="text-[10px] text-blue-500 hover:underline" title={cell.source_url}>
            출처↗
          </a>
        )}
      </div>
    </td>
  );
}

export default function ComparisonMatrixReport({ report }) {
  const [showReliability, setShowReliability] = useState(false);   // 기본 닫힘 (item 6)
  if (!report) return null;
  const content   = report.content ?? {};
  const table     = content.feature_table ?? { columns: [], rows: [] };
  const zone      = content.zone_summary ?? {};
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

  // 라벨 맵 (item 2) — 모든 섹션에서 feature_id 대신 한국어 라벨 사용
  const labelOf = Object.fromEntries(features.map(c => [c.feature_id, c.label]));
  const nameOf  = Object.fromEntries(candidates.map(r => [r.candidate_id, r.candidate_name || r.candidate_id]));
  const scoreRationale = warnings.find(w => String(w).startsWith('score_rationale:'));
  const otherWarnings  = warnings.filter(w => w !== scoreRationale && !String(w).includes('degrade'));

  return (
    <div className="mb-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
      {/* ── 1. 헤더 ── */}
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <h3 className="text-lg font-bold text-gray-900">📊 {content.title ?? '비교 매트릭스'}</h3>
        <ScoreBadge score={report.evaluation_score ?? 0} />
        {degraded && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 font-medium">
            degraded — LLM 판정 생략 (표만 제공)
          </span>
        )}
      </div>
      <p className="text-xs text-gray-400 mb-2">
        {report.rubric_version} · 생성 {String(report.generated_at ?? '').slice(0, 19).replace('T', ' ')}
      </p>
      {/* 루브릭 점수 의미 설명 (item 2·3) */}
      <div className="text-xs bg-indigo-50/60 border border-indigo-100 rounded-lg px-3 py-2.5 mb-4">
        <p className="font-semibold text-indigo-700 mb-1">완성도 점수란?</p>
        <p className="text-gray-600 mb-2">
          상품 간 우열이 아니라 <b>이 비교표 자체가 얼마나 충실하게 작성됐는지</b>를 1~5점으로 평가한 값입니다.
        </p>
        <ul className="space-y-0.5 text-gray-600">
          {RUBRIC_LEVELS.map(lv => (
            <li key={lv.score} className={`flex gap-1.5 ${lv.score === (report.evaluation_score ?? 0) ? 'font-semibold text-indigo-700' : ''}`}>
              <span className="shrink-0">•</span>
              <span><b>{lv.score}점</b> = {lv.text}{lv.score === (report.evaluation_score ?? 0) ? ' (현재)' : ''}</span>
            </li>
          ))}
        </ul>
        {(report.evaluation_score ?? 0) < 5 && NEXT_LEVEL_HINT[report.evaluation_score] && (
          <p className="mt-2 text-indigo-600">
            ↑ 상위 점수까지 부족한 부분: {NEXT_LEVEL_HINT[report.evaluation_score]}
          </p>
        )}
      </div>

      {/* ── 2. feature_table (transpose: 행=항목, 열=상품) ── */}
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
                  <MatrixCell key={c.candidate_id} cell={cellOf(c.candidate_id, f.feature_id)} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── 3. zone_summary ── */}
      {(zone.winning?.length || zone.battling?.length || zone.losing?.length) ? (
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

      {/* ── 4. harvey_balls ── */}
      {harvey.length > 0 && (
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
