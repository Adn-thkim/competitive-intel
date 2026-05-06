/**
 * ResultView
 * ----------
 * competitor_selection 이후 최종 state를 보여주는 화면.
 *
 * 표시 섹션:
 *   1. 자사 상품 요약
 *   2. 선택된 경쟁사 — competition_type별 그룹(direct / indirect / substitute)
 *   3. 선택된 기능적 대안 — 별도 섹션
 *   4. 공식 URL 조회 결과 (official_sources) — 있을 때만 표시
 *   5. Agent 실행 이력
 */

const TYPE_META = {
  direct:     { label: '직접 경쟁',  bg: 'bg-blue-50',   border: 'border-blue-200',   badge: 'bg-blue-100 text-blue-800',   dot: 'bg-blue-500'   },
  indirect:   { label: '간접 경쟁',  bg: 'bg-purple-50', border: 'border-purple-200', badge: 'bg-purple-100 text-purple-800', dot: 'bg-purple-400' },
  substitute: { label: '대체재',     bg: 'bg-orange-50', border: 'border-orange-200', badge: 'bg-orange-100 text-orange-800', dot: 'bg-orange-400' },
};

function confidenceClass(score) {
  if (score >= 0.8)  return 'bg-green-100 text-green-800';
  if (score >= 0.55) return 'bg-yellow-100 text-yellow-800';
  return 'bg-red-100 text-red-700';
}

/* ── 경쟁사 카드 ── */
function CandidateCard({ candidate, isSelected, officialSource }) {
  const meta  = TYPE_META[candidate.competition_type] ?? TYPE_META.substitute;
  const conf  = candidate.confidence ?? 0;

  return (
    <div className={[
      'p-4 rounded-xl border-2 transition-all',
      isSelected ? `${meta.bg} ${meta.border}` : 'bg-white border-gray-100 opacity-50',
    ].join(' ')}>
      <div className="flex flex-wrap items-start gap-2">
        {/* 브랜드 이니셜 */}
        <div className={`w-9 h-9 rounded-full flex items-center justify-center font-bold text-sm shrink-0 ${meta.bg} border ${meta.border}`}>
          {(candidate.brand ?? '?')[0]}
        </div>

        {/* 이름 + 배지 */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
            <span className="font-semibold text-gray-900 text-sm">{candidate.product_name}</span>
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${meta.badge}`}>
              {meta.label}
            </span>
            {isSelected && (
              <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700">
                ✓ 선택됨
              </span>
            )}
            <span className={`ml-auto text-xs font-medium px-2 py-0.5 rounded-full ${confidenceClass(conf)}`}>
              {Math.round(conf * 100)}%
            </span>
          </div>
          <p className="text-xs text-gray-500">{candidate.brand} · {candidate.category}</p>
        </div>
      </div>

      {/* 경쟁 이유 */}
      {candidate.why_competitor?.length > 0 && (
        <ul className="mt-2 text-xs text-gray-600 space-y-0.5 list-disc list-inside pl-1">
          {candidate.why_competitor.slice(0, 2).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}

      {/* 공식 URL 결과 */}
      {officialSource && (
        <div className="mt-2 pt-2 border-t border-gray-100">
          {officialSource.source_type === 'official' && officialSource.primary_url ? (
            <a
              href={officialSource.primary_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-blue-600 hover:underline flex items-center gap-1"
            >
              <span>🔗</span>
              <span className="truncate">{officialSource.primary_url}</span>
              {officialSource.validated && <span className="text-green-600 shrink-0">✓</span>}
            </a>
          ) : officialSource.source_type === 'reference' ? (
            <div className="text-xs text-gray-500">
              <span className="text-amber-600 font-medium">참조 출처</span>
              {officialSource.reference_sources?.slice(0, 2).map((s, i) => (
                <a key={i} href={s.url} target="_blank" rel="noreferrer"
                  className="ml-2 text-blue-500 hover:underline">
                  {s.source_name}
                </a>
              ))}
            </div>
          ) : null}
        </div>
      )}

      {/* candidate_id */}
      <div className="mt-2">
        <code className="text-xs bg-white border border-gray-200 px-1.5 py-0.5 rounded font-mono text-gray-400">
          {candidate.candidate_id}
        </code>
      </div>
    </div>
  );
}

/* ── 기능적 대안 카드 ── */
function FuncCard({ item, isSelected, officialSource }) {
  const conf = item.confidence ?? 0;
  return (
    <div className={[
      'p-4 rounded-xl border-2 transition-all',
      isSelected ? 'bg-teal-50 border-teal-200' : 'bg-white border-gray-100 opacity-50',
    ].join(' ')}>
      <div className="flex flex-wrap items-start gap-2">
        <div className="w-9 h-9 rounded-full bg-teal-100 border border-teal-200 flex items-center justify-center text-teal-700 font-bold text-sm shrink-0">
          {(item.method_name ?? '?')[0]}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
            <span className="font-semibold text-gray-900 text-sm">{item.method_name}</span>
            <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-teal-100 text-teal-800">
              기능적 대안
            </span>
            {isSelected && (
              <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700">
                ✓ 선택됨
              </span>
            )}
            <span className={`ml-auto text-xs font-medium px-2 py-0.5 rounded-full ${confidenceClass(conf)}`}>
              {Math.round(conf * 100)}%
            </span>
          </div>
          <p className="text-xs text-gray-500">{item.provider_type} · {item.category}</p>
        </div>
      </div>

      {item.why_alternative?.length > 0 && (
        <ul className="mt-2 text-xs text-gray-600 space-y-0.5 list-disc list-inside pl-1">
          {item.why_alternative.slice(0, 2).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}

      {/* 참조 출처 (Strategy 1 — reference 분기) */}
      {officialSource?.source_type === 'reference' && (
        <div className="mt-2 pt-2 border-t border-gray-100">
          <p className="text-xs text-amber-600 font-medium mb-1">
            참조 출처 <span className="text-gray-400 font-normal">(공식 URL 없음)</span>
          </p>
          {officialSource.reference_sources?.map((s, i) => (
            <a key={i} href={s.url} target="_blank" rel="noreferrer"
              className="block text-xs text-blue-500 hover:underline truncate">
              {s.source_name} — {s.description}
            </a>
          ))}
          {officialSource.note && (
            <p className="text-xs text-gray-400 mt-1 italic">{officialSource.note}</p>
          )}
        </div>
      )}

      <div className="mt-2">
        <code className="text-xs bg-white border border-gray-200 px-1.5 py-0.5 rounded font-mono text-gray-400">
          {item.candidate_id}
        </code>
      </div>
    </div>
  );
}

/* ── 그룹 섹션 헤더 ── */
function GroupHeader({ type, count }) {
  const meta = TYPE_META[type];
  return (
    <div className="flex items-center gap-2 mb-3">
      <span className={`w-2.5 h-2.5 rounded-full ${meta.dot}`} />
      <h4 className="text-sm font-semibold text-gray-700">{meta.label}</h4>
      <span className="text-xs text-gray-400">{count}개</span>
    </div>
  );
}

/* ── 메인 컴포넌트 ── */
export default function ResultView({ result, onReset }) {
  const state          = result?.state ?? {};
  const candidates     = state.competitor_candidates  ?? [];
  const functional     = state.functional_competitors ?? [];
  const selectedIds    = new Set(state.selected_competitor_ids ?? []);
  const officialSrcs   = state.official_sources ?? [];
  const agentSteps     = state.agent_steps ?? [];
  const ownProduct     = state.own_product  ?? {};
  const errors         = state.errors       ?? [];

  // official_sources → candidate_id 맵
  const srcMap = Object.fromEntries(officialSrcs.map(s => [s.candidate_id, s]));

  // competition_type별 그룹핑 (직접 → 간접 → 대체재 순)
  const grouped = {
    direct:     candidates.filter(c => c.competition_type === 'direct'),
    indirect:   candidates.filter(c => c.competition_type === 'indirect'),
    substitute: candidates.filter(c => c.competition_type === 'substitute'),
  };

  const totalSelected = selectedIds.size;
  const ownOfficialSrc = srcMap[ownProduct.product_id];

  return (
    <div className="min-h-screen bg-gray-50 py-10 px-4">
      <div className="max-w-3xl mx-auto">

        {/* 헤더 */}
        <div className="mb-8 flex items-start justify-between">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">경쟁사 탐색 완료</h2>
            <p className="mt-1 text-sm text-gray-500">
              프로젝트 ID:{' '}
              <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs font-mono text-gray-700">
                {state.project_id ?? '—'}
              </code>
              {totalSelected > 0 && (
                <span className="ml-3 text-indigo-600 font-medium">{totalSelected}개 선택됨</span>
              )}
            </p>
          </div>
          <button
            onClick={onReset}
            className="text-sm text-gray-400 hover:text-gray-600 border border-gray-200 rounded-lg px-3 py-1.5"
          >
            새 분석 시작
          </button>
        </div>

        {/* 에러 배너 */}
        {errors.length > 0 && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl">
            <p className="text-sm font-semibold text-red-700 mb-1">일부 단계에서 오류가 발생했습니다</p>
            {errors.map((e, i) => (
              <p key={i} className="text-xs text-red-600">{e.node}: {e.error}</p>
            ))}
          </div>
        )}

        {/* 자사 상품 요약 */}
        {ownProduct.name && (
          <div className="mb-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              자사 상품
            </h3>
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold text-lg shrink-0">
                {(ownProduct.brand ?? 'O')[0]}
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-gray-900">{ownProduct.name}</p>
                <p className="text-sm text-gray-500">{ownProduct.brand} · {ownProduct.category}</p>
                {ownOfficialSrc?.primary_url && (
                  <a href={ownOfficialSrc.primary_url} target="_blank" rel="noreferrer"
                    className="text-xs text-blue-600 hover:underline flex items-center gap-1 mt-0.5">
                    <span>🔗</span>
                    <span className="truncate">{ownOfficialSrc.primary_url}</span>
                    {ownOfficialSrc.validated && <span className="text-green-600 shrink-0">✓</span>}
                  </a>
                )}
              </div>
              <code className="text-xs bg-gray-100 px-2 py-1 rounded font-mono text-gray-500 shrink-0">
                {ownProduct.product_id}
              </code>
            </div>
          </div>
        )}

        {/* 경쟁사 후보 — competition_type별 섹션 */}
        {candidates.length > 0 && (
          <div className="mb-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-5">
              브랜드 경쟁 상품 ({candidates.length}개)
            </h3>

            {(['direct', 'indirect', 'substitute']).map(type => {
              const group = grouped[type];
              if (!group.length) return null;
              return (
                <div key={type} className="mb-6 last:mb-0">
                  <GroupHeader type={type} count={group.length} />
                  <div className="flex flex-col gap-3">
                    {group.map(c => (
                      <CandidateCard
                        key={c.candidate_id}
                        candidate={c}
                        isSelected={selectedIds.size === 0 || selectedIds.has(c.candidate_id)}
                        officialSource={srcMap[c.candidate_id]}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* 기능적 대안 섹션 */}
        {functional.length > 0 && (
          <div className="mb-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4">
              기능적 대안 수단 ({functional.length}개)
            </h3>
            <div className="flex flex-col gap-3">
              {functional.map(f => (
                <FuncCard
                  key={f.candidate_id}
                  item={f}
                  isSelected={selectedIds.size === 0 || selectedIds.has(f.candidate_id)}
                  officialSource={srcMap[f.candidate_id]}
                />
              ))}
            </div>
          </div>
        )}

        {/* Agent 실행 이력 */}
        {agentSteps.length > 0 && (
          <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Agent 실행 이력
            </h3>
            <div className="flex flex-col gap-2">
              {agentSteps.map((step, i) => (
                <div key={i} className="flex items-center gap-3 text-sm">
                  <span className={`w-2 h-2 rounded-full shrink-0 ${
                    step.status === 'completed' ? 'bg-green-400' :
                    step.status === 'failed'    ? 'bg-red-400'   : 'bg-gray-300'
                  }`} />
                  <span className="text-gray-700 font-medium">{step.step_name}</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                    step.status === 'completed' ? 'bg-green-100 text-green-700' :
                    step.status === 'failed'    ? 'bg-red-100 text-red-700' :
                    'bg-gray-100 text-gray-500'
                  }`}>
                    {step.status}
                  </span>
                  {step.error_message && (
                    <span className="text-xs text-red-500 truncate">{step.error_message}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
