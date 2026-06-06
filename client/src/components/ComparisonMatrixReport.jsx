/**
 * ComparisonMatrixReport
 * ----------------------
 * report_outputs["comparison_matrix"] envelope 렌더 (v0.12 — 간이 확인용).
 * 설계: docs/design/comparison_matrix_node_design.md §3-2 content 구조.
 *
 * 표시 섹션:
 *   1. 헤더 — title · evaluation_score · rubric_version · generated_at
 *   2. feature_table — 행=candidate(own 강조), 열=feature. 셀 표기 규칙(CM-D2):
 *      미확인(회색) · ⚠ 수동검토(빨강) · [기간한정] footnote(보라) · 출처 링크
 *   3. zone_summary — Winning/Battling/Losing 3컬럼 + overall_comment
 *   4. harvey_balls — 정성 feature 5단계 (●○ 표기 + legend)
 *   5. footnotes — promotional(AP-1) · traps(AP-2·AP-3) · warnings
 */

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

function ScoreBadge({ score }) {
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-indigo-100 text-indigo-800 text-sm font-semibold">
      루브릭 {score}/5
      <span className="text-indigo-400 font-normal">{'★'.repeat(score)}{'☆'.repeat(5 - score)}</span>
    </span>
  );
}

function Cell({ cell }) {
  if (!cell) return <td className="px-3 py-2 text-xs text-gray-300">·</td>;
  const meta = STATUS_META[cell.extraction_status] ?? STATUS_META.unknown;
  const isUnknown = cell.extraction_status === 'not_found' || cell.extraction_status === 'unknown';
  return (
    <td className={`px-3 py-2 align-top border-b border-gray-100 ${cell.manual_check_required ? 'bg-red-50/50' : ''}`}>
      <div className={`text-xs leading-relaxed ${isUnknown ? 'text-gray-400 italic' : 'text-gray-800'}`}>
        {cell.display || '미확인'}
        {cell.footnote_refs?.map(r => (
          <sup key={r} className="text-purple-600 font-semibold ml-0.5">[{r}]</sup>
        ))}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${meta.cls}`}>{meta.label}</span>
        {cell.as_of && (
          <span className="text-[10px] text-gray-400">기준 {cell.as_of}</span>
        )}
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

function HarveyBall({ rating }) {
  const glyphs = ['○', '◔', '◑', '◕', '●'];
  return <span className="text-base text-indigo-600">{glyphs[rating] ?? '·'}</span>;
}

export default function ComparisonMatrixReport({ report }) {
  if (!report) return null;
  const content   = report.content ?? {};
  const table     = content.feature_table ?? { columns: [], rows: [] };
  const zone      = content.zone_summary ?? {};
  const harvey    = content.harvey_balls ?? [];
  const promos    = content.promotional_footnotes ?? [];
  const traps     = content.traps_footnote ?? [];
  const warnings  = report.warnings ?? [];
  const degraded  = warnings.some(w => String(w).includes('degrade'));

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
      <p className="text-xs text-gray-400 mb-4">
        {report.rubric_version} · 생성 {String(report.generated_at ?? '').slice(0, 19).replace('T', ' ')}
      </p>

      {/* ── 2. feature_table ── */}
      <div className="overflow-x-auto rounded-xl border border-gray-200 mb-5">
        <table className="min-w-full text-left">
          <thead>
            <tr className="bg-gray-50">
              <th className="px-3 py-2 text-xs font-semibold text-gray-500 sticky left-0 bg-gray-50">상품</th>
              {table.columns.map(col => (
                <th key={col.feature_id} className="px-3 py-2 text-xs font-semibold text-gray-600 min-w-[160px]">
                  {col.label}
                  {col.category && (
                    <span className="block text-[10px] font-normal text-gray-400">{col.category}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map(row => (
              <tr key={row.candidate_id} className={row.is_own ? 'bg-blue-50/60' : ''}>
                <td className={`px-3 py-2 align-top border-b border-gray-100 sticky left-0 ${row.is_own ? 'bg-blue-50' : 'bg-white'}`}>
                  <span className="text-xs font-semibold text-gray-800 whitespace-nowrap">
                    {row.candidate_name || row.candidate_id}
                  </span>
                  {row.is_own && (
                    <span className="block text-[10px] text-blue-600 font-medium">자사</span>
                  )}
                </td>
                {table.columns.map(col => (
                  <Cell key={col.feature_id} cell={row.cells?.[col.feature_id]} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── 3. zone_summary ── */}
      {(zone.winning?.length || zone.battling?.length || zone.losing?.length) ? (
        <div className="mb-5">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Zone 판정 (자사 관점)
          </h4>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {Object.entries(ZONE_META).map(([key, meta]) => (
              <div key={key} className={`rounded-xl border p-3 ${meta.bg} ${meta.border}`}>
                <p className={`text-xs font-bold mb-2 ${meta.text}`}>
                  {meta.title} <span className="font-normal">({meta.sub} {zone[key]?.length ?? 0})</span>
                </p>
                {(zone[key] ?? []).map((z, i) => (
                  <div key={i} className="mb-2 last:mb-0">
                    <code className="text-[10px] bg-white/70 px-1 py-0.5 rounded text-gray-600">
                      {z.feature_id}
                    </code>
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
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            정성 평가 (Harvey Balls)
          </h4>
          {harvey.map((h, i) => (
            <div key={i} className="flex flex-wrap items-center gap-3 text-xs text-gray-700 mb-1.5">
              <code className="bg-gray-100 px-1.5 py-0.5 rounded text-gray-600">{h.feature_id}</code>
              {Object.entries(h.ratings ?? {}).map(([cid, r]) => (
                <span key={cid} className="flex items-center gap-1">
                  <HarveyBall rating={r} />
                  <span className="text-gray-500">{cid}</span>
                </span>
              ))}
              <span className="text-gray-400 italic w-full md:w-auto">범례: {h.legend}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── 5. footnotes ── */}
      {(promos.length > 0 || traps.length > 0 || warnings.length > 0) && (
        <div className="pt-3 border-t border-gray-100 space-y-1">
          {promos.map(f => (
            <p key={f.ref} className="text-xs text-purple-700">
              <sup className="font-semibold">[{f.ref}]</sup> {f.candidate_id} · {f.feature_id} — {f.note}
            </p>
          ))}
          {traps.map((t, i) => (
            <p key={`t${i}`} className="text-xs text-amber-700">⚑ {t}</p>
          ))}
          {warnings.map((w, i) => (
            <p key={`w${i}`} className="text-xs text-gray-400">· {w}</p>
          ))}
        </div>
      )}
    </div>
  );
}
