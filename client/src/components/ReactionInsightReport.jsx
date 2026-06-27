/**
 * ReactionInsightReport (v0.13 — UI 초안)
 * ----------------------------------------
 * report_outputs["reaction_insight"] envelope 렌더.
 * 설계: docs/design/reaction_insight_node_design.md §6·§7
 *
 * 구성 (초안 — 사용자 피드백으로 개선 예정):
 *   1. 헤더 — 제목·완성도 배지·루브릭 설명(§2-2 기준)·부족분 안내
 *   2. 표본 메타 (AP-3) — candidate별 채널 표본·수집 시점 + 채널 가중치
 *   3. aspect × candidate 히트맵 — 가중 sentiment 색상(-1 빨강 ~ +1 초록),
 *      행 클릭 시 해당 aspect 의 quote 만 필터
 *   4. aspect 인사이트 (LLM 서술) — headline + narrative 카드
 *   5. 대표 반응 (quote 카드) — 원문·극성·채널·출처 링크
 *   6. 개선 제안 (suggestions) — product_dev 후보 분리 표시
 *   7. 월별 추이 (timeline) — 반응 수·평균 sentiment 막대
 *   8. 신뢰도·주의 토글 (기본 닫힘) — warnings·score_rationale
 */

import { useEffect, useRef, useState } from 'react';

// 루브릭 §2-2 (reaction_insight) — 1~5점 기준
const RUBRIC_LEVELS = [
  { score: 1, text: '단일 sentiment 점수만 (aspect 미분리)' },
  { score: 2, text: 'aspect 분리되었으나 polarity 만 표기, 강도 없음' },
  { score: 3, text: '7-tuple 확보 — 단일 채널 (YouTube만)' },
  { score: 4, text: '2채널 교차 검증 (YouTube + 커뮤니티)' },
  { score: 5, text: '4점 + 채널 가중치 + 시점 분리 뷰 + 개선 제안 분리' },
];
const NEXT_LEVEL_HINT = {
  1: 'aspect 단위로 의견을 분리하면 2점이 됩니다.',
  2: '강도(intensity)를 포함한 7-tuple 을 갖추면 3점이 됩니다.',
  3: '커뮤니티 채널을 함께 수집해 교차 검증하면 4점이 됩니다.',
  4: '시점 분리 뷰(게시일 50% 이상)와 개선 제안 분리를 갖추면 5점이 됩니다.',
};

const POLARITY_META = {
  positive: { label: '긍정', cls: 'bg-green-100 text-green-700',  border: 'border-green-200' },
  negative: { label: '부정', cls: 'bg-red-100 text-red-700',      border: 'border-red-200' },
  neutral:  { label: '중립', cls: 'bg-gray-100 text-gray-600',    border: 'border-gray-200' },
};
const CHANNEL_META = {
  youtube:   { label: 'YouTube',   cls: 'bg-rose-50 text-rose-600 border-rose-200' },
  community: { label: '커뮤니티',   cls: 'bg-sky-50 text-sky-600 border-sky-200' },
  blog:      { label: '블로그',     cls: 'bg-teal-50 text-teal-600 border-teal-200' },
};

/* 가중 sentiment(-1~+1) → 셀 배경색 (빨강 ~ 회색 ~ 초록) */
function sentimentBg(s) {
  if (s == null) return 'transparent';
  const alpha = Math.min(Math.abs(s), 1) * 0.55 + 0.08;
  return s >= 0 ? `rgba(34,197,94,${alpha})` : `rgba(239,68,68,${alpha})`;
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

function QuoteCard({ q, nameOf }) {
  const pol = POLARITY_META[q.polarity] ?? POLARITY_META.neutral;
  const ch  = CHANNEL_META[q.channel] ?? CHANNEL_META.community;
  return (
    <div className={`rounded-xl border p-3 bg-white ${pol.border}`}>
      <p className="text-sm text-gray-800 leading-relaxed">“{q.quote}”</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]">
        <span className={`px-1.5 py-0.5 rounded-full font-medium ${pol.cls}`}>
          {pol.label} {'!'.repeat(q.intensity ?? 1)}
        </span>
        <span className={`px-1.5 py-0.5 rounded-full border font-medium ${ch.cls}`}>{ch.label}</span>
        <span className="text-gray-500">{nameOf[q.candidate_id] ?? q.candidate_id}</span>
        {q.posted_at && <span className="text-gray-400">{String(q.posted_at).slice(0, 10)}</span>}
        {q.source_url && (
          <a href={q.source_url} target="_blank" rel="noreferrer"
            className="text-blue-500 hover:underline">원문↗</a>
        )}
      </div>
    </div>
  );
}

export default function ReactionInsightReport({ report, candidateNames = {}, ownProductId = '' }) {
  const [showReliability, setShowReliability] = useState(false);
  const [selectedAspect, setSelectedAspect]   = useState(null);
  // 월별 추이 — candidate 필터 (기본: 자사). '' 은 초기 마운트 시 자사로 보정.
  const [timelineCid, setTimelineCid]         = useState('');
  const timelineScrollRef                     = useRef(null);

  // timeline 데이터 (hooks 는 early return 이전에 위치해야 함)
  const timelineRaw = report?.content?.timeline_view ?? {};
  // 형태 호환: v0.13.2 candidate별 분리 / 구형(전체 합산 flat) 모두 지원
  const isPerCandidate = Object.values(timelineRaw).some(
    v => v && typeof v === 'object' && !('count' in v));
  const timelineByCid = isPerCandidate ? timelineRaw : { __all__: timelineRaw };
  const effectiveCid = (timelineCid && timelineByCid[timelineCid]) ? timelineCid
    : (timelineByCid[ownProductId] ? ownProductId : (Object.keys(timelineByCid)[0] ?? ''));
  const timelineSeries = timelineByCid[effectiveCid] ?? {};

  // 최초 렌더·필터 변경 시 우측(최신 시점)으로 스크롤
  useEffect(() => {
    const el = timelineScrollRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [effectiveCid, report]);

  if (!report) return null;

  const content  = report.content ?? {};
  const matrix   = content.aspect_matrix ?? {};
  const labels   = content.aspect_labels ?? {};
  const quotes   = content.top_quotes ?? [];
  const suggests = content.suggestions ?? [];
  const meta     = content.channel_meta ?? {};
  const weights  = content.channel_weights ?? {};
  const insights = content.aspect_insights ?? [];
  const warnings = report.warnings ?? [];
  const score    = report.evaluation_score ?? 0;
  const degraded = warnings.some(w => String(w).includes('degrade'));
  const scoreRationale = warnings.find(w => String(w).startsWith('score_rationale:'));
  const otherWarnings  = warnings.filter(w => w !== scoreRationale && !String(w).includes('degrade'));

  const aspects    = Object.keys(matrix);
  // candidate 정렬 — comparison_matrix 와 동일: 자사 첫 번째, 이후 사전순 (item 1)
  const rawCids    = [...new Set(aspects.flatMap(a => Object.keys(matrix[a])))];
  const candidates = [
    ...(rawCids.includes(ownProductId) ? [ownProductId] : []),
    ...rawCids.filter(c => c !== ownProductId).sort(),
  ];

  // 수집된 채널만 가중치에 표시 — 표본 0인 채널(예: 블로그 미수집)은 숨김
  const DEFAULT_W = { youtube: 1.0, community: 0.9, blog: 0.9 };
  const channelTotals = candidates.reduce((acc, cid) => {
    const cc = meta[cid]?.channel_counts ?? {};
    acc.youtube   += cc.youtube   ?? 0;
    acc.community += cc.community ?? 0;
    acc.blog      += cc.blog      ?? 0;
    return acc;
  }, { youtube: 0, community: 0, blog: 0 });
  const weightLabel = [
    ['YouTube', 'youtube'], ['커뮤니티', 'community'], ['블로그', 'blog'],
  ].filter(([, k]) => channelTotals[k] > 0)
   .map(([label, k]) => `${label} ${weights[k] ?? DEFAULT_W[k]}`)
   .join(' · ');
  const candidateOrder = Object.fromEntries(candidates.map((c, i) => [c, i]));
  const nameOf     = { ...candidateNames };
  const labelOf    = (a) => labels[a] ?? a;

  // 대표 반응 — 긍/부정 분리 + candidate 순서 정렬 (item 2)
  const shownQuotes = selectedAspect
    ? quotes.filter(q => q.aspect === selectedAspect) : quotes;
  const sortByCandidate = qs => [...qs].sort((a, b) =>
    (candidateOrder[a.candidate_id] ?? 99) - (candidateOrder[b.candidate_id] ?? 99)
    || (b.intensity ?? 1) - (a.intensity ?? 1));
  const positiveQuotes = sortByCandidate(shownQuotes.filter(q => q.polarity === 'positive'));
  const negativeQuotes = sortByCandidate(shownQuotes.filter(q => q.polarity === 'negative'));

  // 개선 제안 — 자사 관점만 (item 3)
  const ownSuggestions = suggests.filter(s => s.candidate_id === ownProductId);

  // 항목별 인사이트 — 히트맵 행(aspects) 순서와 통일 (LLM 출력 순서에 비의존)
  const aspectOrder = Object.fromEntries(aspects.map((a, i) => [a, i]));
  const orderedInsights = [...insights].sort((a, b) =>
    (aspectOrder[a.aspect] ?? 99) - (aspectOrder[b.aspect] ?? 99));

  // 개선 제안 — 자사 관점, aspect 별 그룹 (Q2: backend 가 candidate×aspect top-N 으로 상한)
  const suggestionsByAspect = ownSuggestions.reduce((acc, s) => {
    (acc[s.aspect] = acc[s.aspect] || []).push(s);
    return acc;
  }, {});
  const suggestionAspects = Object.keys(suggestionsByAspect)
    .sort((a, b) => (aspectOrder[a] ?? 99) - (aspectOrder[b] ?? 99));

  const maxMonthCount = Math.max(1, ...Object.values(timelineSeries).map(t => t.count ?? 0));

  return (
    <div className="mb-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
      {/* ── 1. 헤더 ── */}
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <h3 className="text-lg font-bold text-gray-900">💬 {content.title ?? '고객 반응 인사이트'}</h3>
        <ScoreBadge score={score} />
        {degraded && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 font-medium">
            degraded — LLM 서술 생략 (집계만 제공)
          </span>
        )}
      </div>
      <p className="text-xs text-gray-400 mb-2">
        {report.rubric_version} · 생성 {String(report.generated_at ?? '').slice(0, 19).replace('T', ' ')}
      </p>
      <div className="text-xs bg-indigo-50/60 border border-indigo-100 rounded-lg px-3 py-2.5 mb-4">
        <p className="font-semibold text-indigo-700 mb-1">완성도 점수란?</p>
        <p className="text-gray-600 mb-2">
          상품 간 우열이 아니라 <b>이 반응 분석이 얼마나 충실하게 수행됐는지</b>를 1~5점으로 평가한 값입니다.
        </p>
        <ul className="space-y-0.5 text-gray-600">
          {RUBRIC_LEVELS.map(lv => (
            <li key={lv.score} className={`flex gap-1.5 ${lv.score === score ? 'font-semibold text-indigo-700' : ''}`}>
              <span className="shrink-0">•</span>
              <span><b>{lv.score}점</b> = {lv.text}{lv.score === score ? ' (현재)' : ''}</span>
            </li>
          ))}
        </ul>
        {score < 5 && NEXT_LEVEL_HINT[score] && (
          <p className="mt-2 text-indigo-600">↑ 상위 점수까지 부족한 부분: {NEXT_LEVEL_HINT[score]}</p>
        )}
      </div>

      {/* ── 2. 표본 메타 (AP-3) + 채널 가중치 — 자사 우선 정렬 (item 1) ── */}
      <div className="flex flex-wrap gap-2 mb-2 text-[11px] text-gray-500">
        {candidates.filter(cid => meta[cid]).map(cid => {
          const counts = meta[cid]?.channel_counts ?? {};
          // 표본 0인 채널은 표기하지 않음. 커뮤니티는 본문/댓글로 분리 표시(split 있을 때).
          const parts = [];
          if ((counts.youtube ?? 0) > 0) parts.push(`YT ${counts.youtube}`);
          if ((counts.community ?? 0) > 0) {
            const hasSplit = counts.community_body != null || counts.community_comment != null;
            parts.push(hasSplit
              ? `커뮤니티 본문 ${counts.community_body ?? 0} · 댓글 ${counts.community_comment ?? 0}`
              : `커뮤니티 ${counts.community}`);
          }
          if ((counts.blog ?? 0) > 0) parts.push(`블로그 ${counts.blog}`);
          return (
            <span key={cid} className="px-2 py-1 rounded-lg bg-gray-50 border border-gray-100">
              <b className="text-gray-700">{nameOf[cid] ?? cid}</b>
              {' '}표본 {meta[cid]?.sample_size ?? 0}건
              {parts.length > 0 && <> ({parts.join(' · ')})</>}
            </span>
          );
        })}
        {weightLabel && (
          <span className="px-2 py-1 rounded-lg bg-indigo-50 border border-indigo-100 text-indigo-600">
            채널 가중치 — {weightLabel}
          </span>
        )}
      </div>
      {/* 채널 가중치 설명 — 전체 표본 카드 하단 */}
      <p className="mb-4 text-[11px] leading-relaxed text-gray-400">
        <b className="text-gray-500">채널 가중치란?</b> 채널마다 신뢰도가 달라, 감성 점수에
        반영하는 비중을 다르게 둔 값입니다. 실명·영상 맥락이 풍부한 YouTube 댓글은 1.0,
        익명성이 강해 표본 편향 가능성이 있는 커뮤니티·블로그 글은 0.9로 약간 낮춰 적용합니다.
        아래 히트맵의 감성 점수는 “각 반응의 극성 × 강도 × 채널 가중치”의 가중 평균이며,
        특정 채널이 결과를 과도하게 좌우하지 않도록 보정하기 위해 사용합니다.
      </p>

      {/* ── 3. aspect × candidate 히트맵 ── */}
      <h4 className="text-sm font-bold text-gray-800 mb-1">반응 히트맵 (aspect × 상품)</h4>
      <p className="text-xs text-gray-600 mb-2">
        색상 = 채널 가중 평균 감성(<span className="text-red-600">빨강 부정</span> ~
        <span className="text-green-600"> 초록 긍정</span>), 숫자 = 반응 수(+긍정/−부정).
        행을 클릭하면 해당 항목의 대표 반응만 모아 봅니다.
      </p>
      <div className="overflow-x-auto rounded-xl border border-gray-200 mb-5">
        <table className="min-w-full text-left border-collapse">
          <thead>
            <tr className="bg-gray-50">
              <th className="px-3 py-2 text-xs font-semibold text-gray-500 min-w-[140px]">반응 항목</th>
              {candidates.map(cid => (
                <th key={cid} className="px-3 py-2 text-xs font-semibold text-gray-600 min-w-[120px] border-l border-gray-200">
                  {nameOf[cid] ?? cid}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {aspects.map(a => (
              <tr key={a}
                onClick={() => setSelectedAspect(selectedAspect === a ? null : a)}
                className={`cursor-pointer hover:bg-gray-50 ${selectedAspect === a ? 'ring-2 ring-inset ring-indigo-300' : ''}`}>
                <td className="px-3 py-2 text-xs font-semibold text-gray-800 border-b border-gray-100 break-keep">
                  {labelOf(a)}
                  {selectedAspect === a && <span className="block text-[10px] text-indigo-500 font-normal">▼ 대표 반응 필터 중</span>}
                </td>
                {candidates.map(cid => {
                  const cell = matrix[a]?.[cid];
                  return (
                    <td key={cid} className="px-3 py-2 border-b border-l border-gray-100 text-center"
                      style={{ backgroundColor: cell ? sentimentBg(cell.weighted_sentiment) : 'transparent' }}>
                      {cell ? (
                        <span className="text-xs text-gray-800">
                          <b>{cell.tuple_count}</b>건
                          <span className="block text-[10px] text-gray-600">
                            +{cell.positive} / −{cell.negative}
                          </span>
                        </span>
                      ) : <span className="text-xs text-gray-300">·</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── 4. aspect 인사이트 (LLM 서술) ── */}
      {insights.length > 0 && (
        <div className="mb-5">
          <h4 className="text-sm font-bold text-gray-800 mb-2">항목별 인사이트</h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {orderedInsights.map((i, idx) => (
              <div key={idx} className="rounded-xl border border-gray-200 p-3 bg-gray-50/50">
                <p className="text-xs font-semibold text-indigo-700 mb-0.5">{labelOf(i.aspect)}</p>
                <p className="text-sm font-semibold text-gray-800 mb-1">{i.headline}</p>
                <p className="text-xs text-gray-600 leading-relaxed">{i.narrative}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 5. 대표 반응 — 좌: 긍정 / 우: 부정, candidate 순 정렬 (item 2) ── */}
      {shownQuotes.length > 0 && (
        <div className="mb-5">
          <h4 className="text-sm font-bold text-gray-800 mb-2">
            대표 반응
            {selectedAspect && (
              <span className="ml-2 text-xs font-normal text-indigo-600">
                — {labelOf(selectedAspect)}만 표시
                <button type="button" onClick={() => setSelectedAspect(null)}
                  className="ml-1 underline">전체 보기</button>
              </span>
            )}
          </h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <p className="text-xs font-semibold text-green-700 mb-2">
                👍 긍정 반응 ({positiveQuotes.length})
              </p>
              <div className="space-y-3">
                {positiveQuotes.map((q, i) => <QuoteCard key={i} q={q} nameOf={nameOf} />)}
                {positiveQuotes.length === 0 && (
                  <p className="text-xs text-gray-400">표시할 긍정 반응이 없습니다.</p>
                )}
              </div>
            </div>
            <div>
              <p className="text-xs font-semibold text-red-700 mb-2">
                👎 부정 반응 ({negativeQuotes.length})
              </p>
              <div className="space-y-3">
                {negativeQuotes.map((q, i) => <QuoteCard key={i} q={q} nameOf={nameOf} />)}
                {negativeQuotes.length === 0 && (
                  <p className="text-xs text-gray-400">표시할 부정 반응이 없습니다.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── 6. 개선 제안 — 자사 관점, 항목(aspect)별 대표 N건 (Q2) ── */}
      {ownSuggestions.length > 0 && (
        <div className="mb-5">
          <h4 className="text-sm font-bold text-gray-800 mb-1">개선 제안 후보 (자사 사용자 요청)</h4>
          <p className="text-xs text-gray-600 mb-2">
            자사 상품 사용자가 직접 요청한 기능·개선 사항을 <b>항목별 대표 요청</b>으로
            정리했습니다 — product 백로그 후보.
          </p>
          <div className="space-y-2.5">
            {suggestionAspects.map(a => (
              <div key={a} className="rounded-lg border border-gray-100 bg-gray-50/50 p-2.5">
                <p className="text-xs font-semibold text-indigo-700 mb-1">{labelOf(a)}</p>
                <div className="space-y-1">
                  {suggestionsByAspect[a].map((s, i) => (
                    <p key={i} className="text-xs text-gray-700">
                      💡 “{s.quote}”
                      {s.source_url && (
                        <a href={s.source_url} target="_blank" rel="noreferrer"
                          className="ml-1 text-blue-500 hover:underline">원문↗</a>
                      )}
                    </p>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 7. 월별 추이 — candidate 드롭다운(자사 default) + 최신 시점 스크롤 ── */}
      {Object.keys(timelineByCid).length > 0 && (
        <div className="mb-5">
          <div className="flex flex-wrap items-center gap-3 mb-1">
            <h4 className="text-sm font-bold text-gray-800">월별 반응 추이</h4>
            <select
              value={effectiveCid}
              onChange={e => setTimelineCid(e.target.value)}
              className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white text-gray-700"
            >
              {Object.keys(timelineByCid)
                .sort((a, b) => (candidateOrder[a] ?? 99) - (candidateOrder[b] ?? 99))
                .map(cid => (
                  <option key={cid} value={cid}>
                    {cid === '__all__' ? '전체 합산' : (nameOf[cid] ?? cid)}
                  </option>
                ))}
            </select>
          </div>
          <p className="text-xs text-gray-600 mb-2">
            선택한 상품의 반응을 월별로 분포한 것입니다 (게시일 보유 반응 한정).
            막대 높이 = 반응 수, 색 = 평균 감성. 최초에는 최신 시점이 보입니다.
          </p>
          {Object.keys(timelineSeries).length > 0 ? (
            <div ref={timelineScrollRef} className="overflow-x-auto rounded-lg border border-gray-100 p-2">
              <div className="flex items-end gap-2 h-24 min-w-max">
                {Object.entries(timelineSeries).map(([month, t]) => (
                  <div key={month} className="flex flex-col items-center gap-1 shrink-0"
                    title={`${month}: ${t.count}건, 평균 감성 ${t.avg_sentiment}`}>
                    <span className="text-[9px] text-gray-500">{t.count}</span>
                    <div className="w-8 rounded-t"
                      style={{ height: `${Math.max(8, (t.count / maxMonthCount) * 60)}px`,
                               backgroundColor: sentimentBg(t.avg_sentiment) }} />
                    <span className="text-[9px] text-gray-400">{month.slice(2)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-xs text-gray-400">선택한 상품의 게시일 보유 반응이 없습니다.</p>
          )}
        </div>
      )}

      {/* ── 8. 신뢰도·주의 토글 ── */}
      {(scoreRationale || otherWarnings.length > 0) && (
        <div className="pt-4 border-t border-gray-100">
          <button type="button" onClick={() => setShowReliability(v => !v)}
            className="w-full flex items-center justify-between text-left group">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider group-hover:text-gray-700">
              리포트 신뢰도·주의 사항
              <span className="ml-2 normal-case font-normal text-gray-400">({warnings.length}건)</span>
            </span>
            <span className="text-gray-400 text-xs">{showReliability ? '▲ 접기' : '▼ 펼치기'}</span>
          </button>
          {showReliability && (
            <div className="mt-2 space-y-1">
              {scoreRationale && (
                <p className="text-xs text-gray-500">{String(scoreRationale).replace('score_rationale:', '점수 근거:')}</p>
              )}
              {otherWarnings.map((w, i) => (
                <p key={i} className="text-xs text-gray-400">· {w}</p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
