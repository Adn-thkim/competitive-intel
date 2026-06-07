/**
 * MarketingSocialReport (v1.0 — UI 초안)
 * ----------------------------------------
 * report_outputs["marketing_social"] envelope 렌더.
 * 설계: docs/design/marketing_social_node_design.md §5 (MS-D4~D7·D10~D14)
 *
 * 구성 (초안 — 사용자 피드백으로 개선 예정):
 *   1. 헤더 — 제목·완성도 배지·루브릭 설명(§2-3 기준)·측정 윈도우
 *   2. PESO 채널 매트릭스 — candidate × platform (measured/presence/none)
 *   3. 채널 운영 표 (4-tuple) — 게시 빈도 2계열·구독자
 *   4. 월별 게시 빈도 — 채널 드롭다운(기본 자사), 전체 vs 상품 관련 2계열 막대
 *   5. Engagement — 분모 2종 병기 (조회수 주 지표)
 *   6. 채널별 키워드 (cross-tab) — 근거 게시물 링크
 *   7. 자사 채널 공백 — coverage_gaps
 *   8. 카피 톤 · 인플루언서 협업 흔적
 *   9. 채널 인사이트 + 종합 서술 (LLM)
 *  10. 신뢰도·주의 토글 (기본 닫힘)
 */

import { useState } from 'react';

// 루브릭 §2-3 (marketing_social) — 1~5점 기준
const RUBRIC_LEVELS = [
  { score: 1, text: '단일 채널 게시물 수만' },
  { score: 2, text: '다채널 PESO 분류, engagement 분모 불명' },
  { score: 3, text: '측정 2종(YouTube·블로그) + PESO + engagement 분모 명기' },
  { score: 4, text: '3점 + 채널 × 키워드 cross-tab' },
  { score: 5, text: '4점 + 동일 기간 정렬 + 자사 채널 공백 식별' },
];
const NEXT_LEVEL_HINT = {
  2: 'engagement 분모를 명기하고 측정 채널을 2종 확보하면 3점이 됩니다.',
  3: '채널 × 키워드 cross-tab(LLM)을 갖추면 4점이 됩니다.',
  4: '동일 기간 정렬과 자사 공백 식별을 갖추면 5점이 됩니다.',
};

const PLATFORM_LABELS = {
  instagram:        'Instagram',
  x:                'X',
  youtube_official: 'YouTube',
  blog_naver:       '네이버 블로그',
  blog_tistory:     '티스토리',
  blog_self_hosted: '자체 블로그',
  press_release:    '보도자료',
};
const PLATFORM_ORDER = [
  'youtube_official', 'blog_naver', 'blog_tistory', 'blog_self_hosted',
  'instagram', 'x', 'press_release',
];

const STATUS_META = {
  measured:       { label: '측정',     cls: 'bg-green-100 text-green-800 border-green-200' },
  measured_empty: { label: '측정·0건', cls: 'bg-green-50 text-green-600 border-green-200' },
  presence_only:  { label: '운영',     cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  none:           { label: '—',        cls: 'bg-gray-50 text-gray-300 border-gray-100' },
};

/* item id → 원문 링크 (youtube video_id 또는 게시물 URL) */
function itemLink(id) {
  return /^https?:/.test(id) ? id : `https://www.youtube.com/watch?v=${id}`;
}

function ScoreBadge({ score }) {
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-indigo-100 text-indigo-800 text-sm font-semibold"
      title="리포트 자체의 완성도 점수 (상품 우열 아님)">
      완성도 {score}/5
      <span className="text-indigo-400 font-normal">{'★'.repeat(score)}{'☆'.repeat(5 - score)}</span>
    </span>
  );
}

function SectionTitle({ children, sub }) {
  return (
    <div className="mb-3">
      <h4 className="text-base font-bold text-gray-800">{children}</h4>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function MarketingSocialReport({ report, candidateNames = {}, ownProductId = '' }) {
  const [showReliability, setShowReliability] = useState(false);
  const [freqKey, setFreqKey] = useState('');

  if (!report) return null;
  const content   = report.content ?? {};
  const peso      = content.peso_matrix ?? {};
  const chMatrix  = content.channel_matrix ?? [];
  const freq      = content.frequency_table ?? {};
  const engage    = content.engagement_table ?? {};
  const crosstab  = content.keyword_crosstab ?? [];
  const gaps      = content.coverage_gaps ?? [];
  const tones     = content.copy_tones ?? [];
  const influ     = content.influencer_signals ?? [];
  const insights  = content.channel_insights ?? [];
  const window_   = content.measurement_window?.months ?? [];
  const judgement = content.related_judgement ?? {};
  // 종합 — v1.0.4 {headline, key_points} 구조 / 구형 문자열 호환
  const rawSummary = content.overall_summary;
  const summary = typeof rawSummary === 'string'
    ? (rawSummary ? { headline: rawSummary, key_points: [] } : null)
    : (rawSummary?.headline || rawSummary?.key_points?.length ? rawSummary : null);
  const warnings  = report.warnings ?? [];
  const score     = report.evaluation_score ?? 0;
  const degraded  = warnings.some(w => String(w).includes('degrade'));
  const rationale = warnings.find(w => String(w).startsWith('score_rationale:'));

  const nameOf = cid => candidateNames[cid] ?? cid;
  // candidate 정렬 — 자사 우선, 이후 이름순 (히트맵 규칙 계승)
  const cidOrder = (a, b) =>
    (a === ownProductId ? -1 : b === ownProductId ? 1 : nameOf(a).localeCompare(nameOf(b)));
  const pesoCids = Object.keys(peso).sort(cidOrder);
  const channelLabel = key => {
    const [cid, type] = [key.slice(0, key.lastIndexOf('/')), key.slice(key.lastIndexOf('/') + 1)];
    return `${nameOf(cid)} · ${type === 'youtube' ? 'YouTube' : (PLATFORM_LABELS[type] ?? type)}`;
  };
  const chKeyOrder = (a, b) => cidOrder(
    a.slice(0, a.lastIndexOf('/')), b.slice(0, b.lastIndexOf('/'))) || a.localeCompare(b);

  // 월별 빈도 — 채널 드롭다운 (기본: 자사 첫 채널)
  const freqKeys = Object.keys(freq).sort(chKeyOrder);
  const effectiveFreqKey = (freqKey && freq[freqKey]) ? freqKey : (freqKeys[0] ?? '');
  const series = freq[effectiveFreqKey]?.monthly ?? {};
  const maxMonthly = Math.max(1, ...window_.map(m => series[m]?.total ?? 0));

  const sortedMatrix = [...chMatrix].sort((a, b) => chKeyOrder(a.channel_key, b.channel_key));
  const maxPerView = Math.max(...Object.values(engage).map(e => e?.per_view_median ?? 0), 0);

  return (
    <div className="mb-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-5">

      {/* 1. 헤더 */}
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <h3 className="text-lg font-bold text-gray-900">📣 {content.title ?? '마케팅·소셜 분석'}</h3>
        <ScoreBadge score={score} />
        {degraded && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 font-medium">
            일부 항목 생략 (LLM degrade)
          </span>
        )}
      </div>
      <p className="text-xs text-gray-400 mb-3">
        {report.rubric_version} · 생성 {String(report.generated_at ?? '').slice(0, 19).replace('T', ' ')}
        {window_.length > 0 && <> · 측정 기간 {window_[0]} ~ {window_[window_.length - 1]} (동일 윈도우)</>}
      </p>

      {/* 루브릭 설명 */}
      <div className="mb-5 rounded-xl bg-indigo-50/60 border border-indigo-100 p-3 text-xs text-indigo-900">
        <p className="font-semibold mb-1.5">완성도 점수 기준 (자사·경쟁사 채널 운영 분석의 충실도)</p>
        <ul className="space-y-0.5">
          {RUBRIC_LEVELS.map(l => (
            <li key={l.score} className={l.score === score ? 'font-bold' : 'text-indigo-700/70'}>
              • {l.score}점 — {l.text}{l.score === score && ' ← 현재'}
            </li>
          ))}
        </ul>
        {score < 5 && NEXT_LEVEL_HINT[score] && (
          <p className="mt-1.5 text-indigo-600">↑ {NEXT_LEVEL_HINT[score]}</p>
        )}
      </div>

      {/* 2. PESO 채널 매트릭스 */}
      <SectionTitle sub="채널별 측정 상태 — 측정: 게시 데이터 수집 완료 · 운영: 채널 존재 확인(API 제약 등으로 측정 보류) · —: 미운영/미발견">
        채널 커버리지
      </SectionTitle>
      <div className="overflow-x-auto mb-6">
        <table className="text-xs border-collapse min-w-full">
          <thead>
            <tr>
              <th className="text-left py-1.5 pr-3 text-gray-500 font-medium whitespace-nowrap">candidate</th>
              {PLATFORM_ORDER.map(p => (
                <th key={p} className="px-2 py-1.5 text-gray-500 font-medium whitespace-nowrap">{PLATFORM_LABELS[p]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pesoCids.map(cid => (
              <tr key={cid} className="border-t border-gray-100">
                <td className="py-1.5 pr-3 font-medium text-gray-800 whitespace-nowrap">{nameOf(cid)}</td>
                {PLATFORM_ORDER.map(p => {
                  const st = STATUS_META[peso[cid]?.[p]] ?? STATUS_META.none;
                  return (
                    <td key={p} className="px-2 py-1.5 text-center">
                      <span className={`inline-block min-w-[42px] px-1.5 py-0.5 rounded border font-medium ${st.cls}`}>
                        {st.label}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 3. 채널 운영 표 (4-tuple) */}
      <SectionTitle sub={`모집단: 측정 윈도우(${window_[0] ?? ''}~${window_[window_.length - 1] ?? ''}, 최근 ${window_.length}개월) 내 게시물. 상품 관련 = 분석 대상 상품을 직접 다루는 게시물`}>
        채널 운영 지표
      </SectionTitle>
      <div className="overflow-x-auto mb-6">
        <table className="text-xs border-collapse min-w-full">
          <thead>
            <tr className="text-gray-500">
              <th className="text-left py-1.5 pr-3 font-medium whitespace-nowrap">채널</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">게시 (윈도우)</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">상품 관련</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">관련 비중</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">구독/규모</th>
            </tr>
          </thead>
          <tbody>
            {sortedMatrix.map(row => {
              const f = freq[row.channel_key] ?? {};
              const ratio = f.window_total ? Math.round((f.related_total / f.window_total) * 100) : 0;
              return (
                <tr key={row.channel_key} className="border-t border-gray-100">
                  <td className="py-1.5 pr-3 font-medium text-gray-800 whitespace-nowrap">
                    {channelLabel(row.channel_key)}
                    {row.platforms.length > 1 && (
                      <span className="ml-1 text-[10px] text-gray-400" title={`동일 블로그 병합: ${row.platforms.join(' + ')}`}>
                        (병합)
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-center text-gray-700">
                    {row.posting_frequency}
                    {f.window_total === 0 && (
                      <span className="ml-1 text-[10px] text-gray-400">게시 없음</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-center font-semibold text-indigo-700">{row.product_related}</td>
                  <td className="px-2 py-1.5 text-center text-gray-500">{f.window_total ? `${ratio}%` : '—'}</td>
                  <td className="px-2 py-1.5 text-center text-gray-700">
                    {row.audience_size != null ? row.audience_size.toLocaleString() : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* 4. 월별 게시 빈도 (2계열) */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <SectionTitle sub="회색: 전체 게시 · 남색: 상품 관련 게시">월별 게시 빈도</SectionTitle>
        <select
          value={effectiveFreqKey}
          onChange={e => setFreqKey(e.target.value)}
          className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-white text-gray-700"
        >
          {freqKeys.map(k => <option key={k} value={k}>{channelLabel(k)}</option>)}
        </select>
      </div>
      <div className="overflow-x-auto mb-1">
        <div className="flex items-end gap-3 h-32 min-w-[360px] px-1">
          {window_.map(m => {
            const cell = series[m] ?? { total: 0, product_related: 0 };
            const hTotal = Math.round((cell.total / maxMonthly) * 100);
            const hRel   = Math.round((cell.product_related / maxMonthly) * 100);
            return (
              <div key={m} className="flex flex-col items-center gap-1 flex-1">
                <div className="flex items-end gap-0.5 h-24">
                  <div className="w-4 rounded-t bg-gray-300" style={{ height: `${hTotal}%` }}
                    title={`전체 ${cell.total}건`} />
                  <div className="w-4 rounded-t bg-indigo-500" style={{ height: `${hRel}%` }}
                    title={`상품 관련 ${cell.product_related}건`} />
                </div>
                <span className="text-[10px] text-gray-400">{m.slice(2)}</span>
                <span className="text-[10px] text-gray-600 font-medium">{cell.total}·{cell.product_related}</span>
              </div>
            );
          })}
        </div>
      </div>
      <p className="text-[11px] text-gray-400 mb-6">
        윈도우 합계 {freq[effectiveFreqKey]?.window_total ?? 0}건 · 상품 관련{' '}
        {freq[effectiveFreqKey]?.related_total ?? 0}건
        {(freq[effectiveFreqKey]?.window_total ?? 0) === 0 && ' — 최근 6개월 게시 없음 (그 자체가 운영 신호)'}
      </p>

      {/* 5. Engagement */}
      <div className="rounded-lg bg-rose-50 border border-rose-200 px-3 py-2 mb-1 inline-block">
        <span className="text-xs font-bold text-rose-700">📺 YouTube 전용 지표</span>
      </div>
      <SectionTitle sub="모집단: 수집된 최근 영상 전체(채널당 최대 100개) — 위 '게시 빈도'(6/12개월 윈도우)와 표본이 다름. 주 지표 = (좋아요+댓글)÷조회수 중앙값. 구독자 분모는 영상별 도달 변동을 왜곡하므로 보조. ※ YouTube는 2021년 싫어요 수 API 제공을 중단해 싫어요 기반 지표는 산출 불가">
        Engagement
      </SectionTitle>
      <div className="overflow-x-auto mb-6">
        <table className="text-xs border-collapse min-w-full">
          <thead>
            <tr className="text-gray-500">
              <th className="text-left py-1.5 pr-3 font-medium">candidate</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">구독자</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">조회수 (합)</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">좋아요 (합)</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">댓글 (합)</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">반응률·조회 기준 (주)</th>
              <th className="px-2 py-1.5 font-medium whitespace-nowrap">반응률·구독 기준 (보조)</th>
              <th className="px-2 py-1.5 font-medium">표본</th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(engage).sort(cidOrder).map(cid => {
              const e = engage[cid] ?? {};
              const top = e.per_view_median != null && e.per_view_median === maxPerView;
              const n = v => (v != null ? v.toLocaleString() : '—');
              return (
                <tr key={cid} className="border-t border-gray-100">
                  <td className="py-1.5 pr-3 font-medium text-gray-800 whitespace-nowrap">{nameOf(cid)}</td>
                  <td className="px-2 py-1.5 text-center text-gray-700">{n(e.subscriber_count)}</td>
                  <td className="px-2 py-1.5 text-center text-gray-700">{n(e.total_views)}</td>
                  <td className="px-2 py-1.5 text-center text-gray-700">{n(e.total_likes)}</td>
                  <td className="px-2 py-1.5 text-center text-gray-700">{n(e.total_comments)}</td>
                  <td className={`px-2 py-1.5 text-center ${top ? 'font-bold text-green-700' : 'text-gray-700'}`}>
                    {e.per_view_median != null ? `${(e.per_view_median * 100).toFixed(2)}%` : '—'}
                    {top && ' ▲'}
                  </td>
                  <td className="px-2 py-1.5 text-center text-gray-500">
                    {e.per_subscriber_median != null ? `${(e.per_subscriber_median * 100).toFixed(3)}%` : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-center text-gray-500">{e.sample_size ?? '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* 6. 채널별 키워드 (cross-tab) */}
      {crosstab.length > 0 && (
        <>
          <SectionTitle sub="채널별 상위 주제 키워드 (LLM 추출 · 근거 게시물 링크)">채널 × 키워드</SectionTitle>
          <div className="grid gap-3 mb-6 sm:grid-cols-2">
            {[...crosstab].sort((a, b) => chKeyOrder(a.channel_key, b.channel_key)).map(e => (
              <div key={e.channel_key} className="rounded-xl border border-gray-200 p-3">
                <p className="text-xs font-semibold text-gray-700 mb-2">{channelLabel(e.channel_key)}</p>
                <div className="flex flex-wrap gap-1.5">
                  {e.keywords.map(kw => (
                    <span key={kw.keyword}
                      className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-full bg-gray-100 text-gray-700">
                      {kw.keyword}
                      {kw.example_ids?.[0] && (
                        <a href={itemLink(kw.example_ids[0])} target="_blank" rel="noreferrer"
                          className="text-blue-500 hover:underline" title="근거 게시물">↗</a>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* 7. 자사 채널 공백 */}
      {gaps.length > 0 && (
        <>
          <SectionTitle sub="경쟁사는 운영하지만 자사는 미운영인 채널">자사 채널 공백</SectionTitle>
          <div className="flex flex-wrap gap-2 mb-6">
            {gaps.map(g => (
              <div key={g.platform} className="rounded-xl border border-red-200 bg-red-50/60 px-3 py-2 text-xs">
                <span className="font-semibold text-red-700">{PLATFORM_LABELS[g.platform] ?? g.platform}</span>
                <span className="text-red-500 ml-2">
                  보유: {g.held_by.map(nameOf).join(', ')}
                </span>
              </div>
            ))}
          </div>
        </>
      )}

      {/* 8. 카피 톤 · 인플루언서 협업 */}
      {(tones.length > 0 || influ.length > 0) && (
        <div className="grid gap-3 mb-6 lg:grid-cols-2">
          {tones.length > 0 && (
            <div>
              <SectionTitle sub="후보별 캠페인 카피·소구 방식 (LLM 분석)">카피 톤</SectionTitle>
              <div className="space-y-2">
                {[...tones].sort((a, b) => cidOrder(a.candidate_id, b.candidate_id)).map(t => (
                  <div key={t.candidate_id} className="rounded-xl border border-gray-200 p-3">
                    <p className="text-xs font-semibold text-gray-700 mb-1">{nameOf(t.candidate_id)}</p>
                    <p className="text-xs text-gray-600 leading-relaxed">{t.tone_summary}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
          {influ.length > 0 && (
            <div>
              <SectionTitle sub="협찬·콜라보 표기가 확인된 후보만">인플루언서 협업 흔적</SectionTitle>
              <div className="space-y-2">
                {[...influ].sort((a, b) => cidOrder(a.candidate_id, b.candidate_id)).map(s => (
                  <div key={s.candidate_id} className="rounded-xl border border-purple-200 bg-purple-50/40 p-3">
                    <p className="text-xs font-semibold text-purple-800 mb-1">
                      {nameOf(s.candidate_id)}
                      <span className="ml-1.5 font-normal text-purple-500">근거 {s.evidence_ids.length}건</span>
                    </p>
                    <p className="text-xs text-gray-600 leading-relaxed">{s.note}</p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {s.evidence_ids.slice(0, 4).map(id => (
                        <a key={id} href={itemLink(id)} target="_blank" rel="noreferrer"
                          className="text-[10px] text-blue-500 hover:underline">원문↗</a>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* 9. 채널 인사이트 + 종합 */}
      {insights.length > 0 && (
        <>
          <SectionTitle>채널별 인사이트</SectionTitle>
          <div className="space-y-2 mb-6">
            {[...insights].sort((a, b) => chKeyOrder(a.channel_key, b.channel_key)).map(i => (
              <div key={i.channel_key} className="rounded-xl border border-gray-200 p-3">
                <p className="text-xs font-semibold text-gray-700 mb-1">{channelLabel(i.channel_key)}</p>
                <p className="text-xs text-gray-600 leading-relaxed">{i.insight}</p>
              </div>
            ))}
          </div>
        </>
      )}
      {summary && (
        <div className="mb-5 rounded-xl bg-gray-50 border border-gray-200 p-4">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">종합</p>
          {summary.headline && (
            <p className="text-sm font-bold text-gray-900 leading-relaxed mb-2.5">{summary.headline}</p>
          )}
          {summary.key_points?.length > 0 && (
            <ul className="space-y-1.5">
              {summary.key_points.map((kp, i) => (
                <li key={i} className="flex gap-2 text-sm text-gray-700 leading-relaxed">
                  <span className="shrink-0 mt-0.5 inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-200 text-gray-600 whitespace-nowrap">
                    {kp.label}
                  </span>
                  <span>{kp.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* 10. 신뢰도·주의 토글 */}
      <button
        type="button"
        onClick={() => setShowReliability(v => !v)}
        className="text-xs text-gray-400 hover:text-gray-600"
      >
        {showReliability ? '▾' : '▸'} 신뢰도·주의 사항
      </button>
      {showReliability && (
        <div className="mt-2 rounded-xl bg-gray-50 border border-gray-200 p-3 text-[11px] text-gray-500 space-y-1">
          {rationale && <p>• {rationale.replace('score_rationale:', '점수 근거:')}</p>}
          <p>• 상품 관련 판정: 상품명 직접 포함 {judgement.prejudged ?? 0}건(코드 확정) + 문맥 판정 {judgement.llm_added ?? 0}건(LLM)</p>
          <p>• Instagram·X·보도자료는 API/수집 제약으로 존재 여부만 표기 (측정 제외)</p>
          {warnings.filter(w => !String(w).startsWith('score_rationale:')).map((w, i) => (
            <p key={i}>• {w}</p>
          ))}
        </div>
      )}
    </div>
  );
}
