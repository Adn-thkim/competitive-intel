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

import { useEffect, useState } from 'react';
import { pspKo, qtKo } from './reactionLabels';

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

/* 셀 극성 판정 — weighted_sentiment(채널 가중 평균 감성) 부호 기준.
 * 데이터 없거나(tuple_count 0) 정확히 0(중립)이면 null(강조 제외). */
function cellPolarity(cell) {
  if (!cell || !(cell.tuple_count > 0)) return null;
  const s = cell.weighted_sentiment;
  if (s == null || s === 0) return null;
  return s < 0 ? 'neg' : 'pos';
}

/* 행 강조 스타일 — 3종 모두 행 전체 테두리. 색상은 구분되게 amber/red/green.
 * Tailwind JIT 스캔을 위해 클래스는 리터럴로 둔다. */
const ROW_MARK = {
  allNeg: {
    label: 'border-y-[3px] border-l-[3px] border-amber-400',
    cell:  'border-y-[3px] border-amber-400 border-l border-l-amber-200',
    last:  'border-r-[3px] border-r-amber-400',
    text:  '⚠ 전 상품 공통 부정', textCls: 'text-amber-600',
  },
  ownNeg: {
    label: 'border-y-[3px] border-l-[3px] border-red-500',
    cell:  'border-y-[3px] border-red-500 border-l border-l-red-200',
    last:  'border-r-[3px] border-r-red-500',
    text:  '⚠ 자사만 부정', textCls: 'text-red-600',
  },
  ownPos: {
    label: 'border-y-[3px] border-l-[3px] border-green-500',
    cell:  'border-y-[3px] border-green-500 border-l border-l-green-200',
    last:  'border-r-[3px] border-r-green-500',
    text:  '★ 자사만 긍정', textCls: 'text-green-600',
  },
};

// 두괄식 — 자사 감성 vs 경쟁 평균 격차 임계 + 강점/접전/약점 태그
const SENTIMENT_GAP = 0.15;

// (월간 추이 MiniTrend·aspectLines·TREND_MONTHS_LIMIT 는 신 파이프라인 리포트에서 제거 — 2026-08)

// 완성도 점수·루브릭 설명을 콘솔에 출력 (웹 페이지 비노출 — 비교 매트릭스와 동일 처리).
function logRubric(score) {
  const lines = RUBRIC_LEVELS.map(
    lv => `  ${lv.score}점 = ${lv.text}${lv.score === score ? '  ← 현재' : ''}`);
  const hint = (score < 5 && NEXT_LEVEL_HINT[score])
    ? `\n  ↑ 상위 점수까지: ${NEXT_LEVEL_HINT[score]}` : '';
  console.info(
    `[고객 반응 인사이트] 완성도 ${score}/5  ${'★'.repeat(score)}${'☆'.repeat(5 - score)}\n` +
    '(상품 우열이 아니라 반응 분석 자체의 충실도 점수)\n' +
    lines.join('\n') + hint);
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
        {q.perspective && (
          <span className="px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-600 border border-indigo-100 font-medium">
            {pspKo(q.perspective)}
          </span>
        )}
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

/* 분포 가로 막대 — most_common 정렬 dist({key:count}). 대표(1위)를 ★+굵게+진한 색으로 강조.
   ownDist 제공 시 각 행에 자사 분포를 두 번째 막대(에메랄드)로 겹쳐 표시(각 계열 내부 max 정규화, %=계열 내 점유율). */
function DistBar({ title, subtitle, dist, ownDist, labelOf, ownFirst = false }) {
  const entries = Object.entries(dist || {});
  if (entries.length === 0) return null;
  const total = entries.reduce((s, [, n]) => s + n, 0) || 1;
  const max = entries[0][1] || 1;
  const ownTotal = ownDist ? (Object.values(ownDist).reduce((s, n) => s + n, 0) || 1) : 0;
  const ownMax = ownDist ? (Math.max(...entries.map(([k]) => ownDist[k] || 0)) || 1) : 1;
  return (
    <div className="flex-1 min-w-[260px]">
      <h4 className="text-sm font-bold text-gray-800 mb-0.5">{title}</h4>
      {subtitle && <p className="text-[11px] text-gray-400 mb-1">{subtitle}</p>}
      {ownDist && (
        <div className="flex items-center gap-3 text-[10px] text-gray-500 mb-1.5">
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-sm bg-indigo-400" />전체</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-sm bg-emerald-500" />자사</span>
        </div>
      )}
      <div className="space-y-1">
        {entries.map(([k, n], i) => {
          const pct = Math.round((n / total) * 100);
          const top = i === 0;
          const on = ownDist ? (ownDist[k] || 0) : 0;
          const opct = ownDist ? Math.round((on / ownTotal) * 100) : 0;
          return (
            <div key={k} className={`flex ${ownDist ? 'items-start' : 'items-center'} gap-2 text-xs`}>
              <span className={`w-28 shrink-0 text-right ${ownDist ? 'pt-0.5' : ''} ${top ? 'font-bold text-indigo-700' : 'text-gray-600'}`}>
                {top && '★ '}{labelOf(k)}
              </span>
              <div className="flex-1 space-y-0.5">
                {(() => {
                  const overallBar = (
                    <div className="flex items-center gap-1.5">
                      <div className="flex-1 h-3.5 bg-gray-100 rounded overflow-hidden">
                        <div className={`h-3.5 rounded ${top ? 'bg-indigo-500' : 'bg-indigo-300'}`}
                          style={{ width: `${Math.max((n / max) * 100, 3)}%` }} />
                      </div>
                      <span className="w-16 shrink-0 text-gray-500 tabular-nums">{n} ({pct}%)</span>
                    </div>
                  );
                  const ownBar = ownDist ? (
                    <div className="flex items-center gap-1.5">
                      <div className="flex-1 h-2.5 bg-gray-50 rounded overflow-hidden">
                        <div className="h-2.5 rounded bg-emerald-500"
                          style={{ width: `${on > 0 ? Math.max((on / ownMax) * 100, 3) : 0}%` }} />
                      </div>
                      <span className="w-16 shrink-0 text-emerald-600 tabular-nums">{on} ({opct}%)</span>
                    </div>
                  ) : null;
                  return ownFirst ? <>{ownBar}{overallBar}</> : <>{overallBar}{ownBar}</>;
                })()}
              </div>
            </div>
          );
        })}
      </div>
      {ownDist && (() => {
        // 결정론 캡션(무 LLM): ① 집중도 = 상위 M개가 전체의 ≥50% · ② 자사 편향 = 점유율차(%p) ±5 이상 over/under.
        let acc = 0, m = 0;
        for (const [, n] of entries) { acc += n; m += 1; if (acc >= total * 0.5) break; }
        const coverPct = Math.round((acc / total) * 100);
        const topNames = entries.slice(0, m).map(([k]) => labelOf(k)).join('/');
        const hasOwn = Object.values(ownDist).some(v => v > 0);
        const TH = 5;
        const bias = hasOwn ? entries.map(([k, n]) => {
          const ownP = Math.round(((ownDist[k] || 0) / ownTotal) * 100);
          const allP = Math.round((n / total) * 100);
          return { k, ownP, allP, diff: ownP - allP };
        }) : [];
        const over  = bias.filter(b => b.diff >= TH).sort((a, b) => b.diff - a.diff)[0];
        const under = bias.filter(b => b.diff <= -TH).sort((a, b) => a.diff - b.diff)[0];
        return (
          <p className="mt-2 text-[11px] text-gray-500 leading-snug">
            상위 {m}개({topNames})가 전체 의견의 {coverPct}%.
            {!hasOwn ? '' : (over || under)
              ? <> 자사는{over && <> ‘{labelOf(over.k)}’ 비중이 전체보다 높고(자사 {over.ownP}% vs 전체 {over.allP}%)</>}
                  {over && under && ','}{under && <> ‘{labelOf(under.k)}’는 낮음(자사 {under.ownP}% vs 전체 {under.allP}%)</>}.</>
              : ' 자사·전체 분포 편향은 뚜렷하지 않음.'}
          </p>
        );
      })()}
    </div>
  );
}

/* 비율 파이 — entries=[{p,tot,...}], total. 우측 범례에 라벨(labelOf)·%. perspective·question_type 공용. */
const PSP_COLORS = ['#6366f1', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4', '#a855f7', '#84cc16', '#94a3b8'];
function PspPie({ entries, total, size = 132, labelOf = pspKo }) {
  const cx = size / 2, cy = size / 2, r = size / 2 - 2;
  let acc = 0;
  const slices = entries.map((e, i) => {
    const a0 = (acc / total) * 2 * Math.PI, a1 = ((acc + e.tot) / total) * 2 * Math.PI;
    acc += e.tot;
    const x0 = cx + r * Math.sin(a0), y0 = cy - r * Math.cos(a0);
    const x1 = cx + r * Math.sin(a1), y1 = cy - r * Math.cos(a1);
    const large = (a1 - a0) > Math.PI ? 1 : 0;
    return { path: `M${cx},${cy} L${x0},${y0} A${r},${r} 0 ${large} 1 ${x1},${y1} Z`,
             color: PSP_COLORS[i % PSP_COLORS.length], e };
  });
  return (
    <div className="flex items-center gap-3 shrink-0">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {entries.length === 1
          ? <circle cx={cx} cy={cy} r={r} fill={PSP_COLORS[0]} />
          : slices.map((s, i) => <path key={i} d={s.path} fill={s.color} stroke="#fff" strokeWidth="1" />)}
      </svg>
      <div className="text-[11px] space-y-0.5">
        {entries.map((e, i) => (
          <div key={e.p} className="flex items-center gap-1.5 whitespace-nowrap">
            <span className="inline-block w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: PSP_COLORS[i % PSP_COLORS.length] }} />
            <span className="text-gray-700">{labelOf(e.p)}</span>
            <span className="text-gray-400">{Math.round((e.tot / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* 긍정/중립/부정 가로 누적 막대 */
function PolarityBar({ pos, neu, neg }) {
  const tot = (pos + neu + neg) || 1;
  const w = n => `${(n / tot) * 100}%`;
  return (
    <div>
      <div className="flex h-4 rounded overflow-hidden text-[9px] font-medium">
        {pos > 0 && <div style={{ width: w(pos), background: '#22c55e' }} className="flex items-center justify-center text-white">{Math.round((pos / tot) * 100)}%</div>}
        {neu > 0 && <div style={{ width: w(neu), background: '#cbd5e1' }} className="flex items-center justify-center text-gray-600">{Math.round((neu / tot) * 100)}%</div>}
        {neg > 0 && <div style={{ width: w(neg), background: '#ef4444' }} className="flex items-center justify-center text-white">{Math.round((neg / tot) * 100)}%</div>}
      </div>
      <div className="text-[10px] text-gray-400 mt-0.5">긍정 {pos} · 중립 {neu} · 부정 {neg}</div>
    </div>
  );
}

/* LLM 분석 렌더 — positives/negatives/competition(정성+수치 병합) bullet. */
function AnalysisView({ data }) {
  const Section = ({ label, items, color, dot }) => (items && items.length) ? (
    <div className="mb-2.5 last:mb-0">
      <p className={`text-[11px] font-bold ${color} mb-1 flex items-center gap-1`}>
        <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: dot }} />{label}
      </p>
      <ul className="space-y-1 pl-0.5">
        {items.map((s, i) => (
          <li key={i} className="text-gray-700 leading-snug flex gap-1.5">
            <span className="text-gray-300 shrink-0">·</span><span>{s}</span>
          </li>
        ))}
      </ul>
    </div>
  ) : null;
  if (!data) return <span className="text-gray-400">분석 생성 전입니다(재실행 시 표시).</span>;
  if (typeof data === 'string') return <p className="whitespace-pre-line leading-relaxed text-gray-700">{data}</p>;
  const empty = !((data.positives || []).length || (data.negatives || []).length || (data.competition || []).length);
  if (empty) return <span className="text-gray-400">요약할 근거가 부족합니다.</span>;
  return (
    <div>
      <Section label="자사 긍정" items={data.positives} color="text-green-700" dot="#22c55e" />
      <Section label="자사 부정" items={data.negatives} color="text-red-600" dot="#ef4444" />
      <Section label="경쟁 비교" items={data.competition} color="text-indigo-700" dot="#6366f1" />
    </div>
  );
}

/* 자사 질문 유형 LLM 분석 렌더 — focus(자사 질문 초점)/competition(경쟁 비교) bullet. */
function QuestionAnalysisView({ data }) {
  const Section = ({ label, items, color, dot }) => (items && items.length) ? (
    <div className="mb-2.5 last:mb-0">
      <p className={`text-[11px] font-bold ${color} mb-1 flex items-center gap-1`}>
        <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: dot }} />{label}
      </p>
      <ul className="space-y-1 pl-0.5">
        {items.map((s, i) => (
          <li key={i} className="text-gray-700 leading-snug flex gap-1.5">
            <span className="text-gray-300 shrink-0">·</span><span>{s}</span>
          </li>
        ))}
      </ul>
    </div>
  ) : null;
  if (!data || !((data.focus || []).length || (data.competition || []).length))
    return <span className="text-gray-400">분석 생성 전입니다(재실행 시 표시).</span>;
  return (
    <div>
      <Section label="자사 질문 초점" items={data.focus} color="text-indigo-700" dot="#6366f1" />
      <Section label="경쟁 비교" items={data.competition} color="text-emerald-700" dot="#10b981" />
    </div>
  );
}

/* 질문량(x) × 의견 부정률(y) 산점도 — 반응 항목별. 자사 강조(에메랄드), 경쟁사 회색.
   '질문 많고 의견 부정↑ = 실질 불만' vs '질문 많고 의견 적음 = 단순 혼란' 을 분리해 읽게 한다. */
function QScatter({ qabc, matrix, own, candidates, nameOf, TOPN = 6 }) {
  const rate = c => (c && c.tuple_count > 0) ? Math.round(((c.negative || 0) / c.tuple_count) * 100) : null;
  // 상위 항목(질문 총량 desc) 선정 → 항목×candidate 점 생성(의견 데이터 있는 것만).
  const totals = Object.entries(qabc || {})
    .filter(([sa]) => sa !== '종합' && sa !== '기타')
    .map(([sa, cids]) => [sa, Object.values(cids).reduce((s, n) => s + n, 0)])
    .sort((a, b) => b[1] - a[1]).slice(0, TOPN).map(([sa]) => sa);
  const pts = [];
  for (const sa of totals) {
    for (const cid of Object.keys(qabc[sa] || {})) {
      const y = rate(matrix[sa]?.[cid]);
      if (y == null) continue;
      pts.push({ x: qabc[sa][cid], y, own: cid === own, sa });
    }
  }
  if (pts.length === 0) return null;
  const W = 300, H = 210, ml = 32, mb = 26, mt = 8, mr = 10;
  const pw = W - ml - mr, ph = H - mt - mb;
  const maxX = Math.max(...pts.map(p => p.x), 1);
  const sx = v => ml + (v / maxX) * pw;
  const sy = v => mt + (1 - v / 100) * ph;
  const own_pts = pts.filter(p => p.own), comp_pts = pts.filter(p => !p.own);
  return (
    <div className="shrink-0 w-[300px]">
      <p className="text-xs font-bold text-gray-700 mb-1">질문량 × 의견 부정률
        <span className="font-normal text-gray-400"> · 상위 {totals.length}개 항목</span></p>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="overflow-visible">
        {/* 축 */}
        <line x1={ml} y1={mt} x2={ml} y2={mt + ph} stroke="#e5e7eb" />
        <line x1={ml} y1={mt + ph} x2={ml + pw} y2={mt + ph} stroke="#e5e7eb" />
        {[0, 50, 100].map(t => (
          <g key={t}>
            <line x1={ml} y1={sy(t)} x2={ml + pw} y2={sy(t)} stroke="#f3f4f6" />
            <text x={ml - 4} y={sy(t) + 3} textAnchor="end" className="fill-gray-400" style={{ fontSize: 9 }}>{t}</text>
          </g>
        ))}
        <text x={ml + pw} y={mt + ph + 16} textAnchor="end" className="fill-gray-400" style={{ fontSize: 9 }}>질문 수 →</text>
        <text x={ml - 26} y={mt + 6} className="fill-gray-400" style={{ fontSize: 9 }}>부정률%</text>
        {comp_pts.map((p, i) => <circle key={`c${i}`} cx={sx(p.x)} cy={sy(p.y)} r="3.5" fill="#94a3b8" fillOpacity="0.7" />)}
        {own_pts.map((p, i) => (
          <g key={`o${i}`}>
            <circle cx={sx(p.x)} cy={sy(p.y)} r="6" fill="#10b981" stroke="#065f46" strokeWidth="1.5" />
            <text x={sx(p.x) + 8} y={sy(p.y) + 3} className="fill-emerald-700 font-semibold" style={{ fontSize: 9 }}>{p.sa}</text>
          </g>
        ))}
      </svg>
      <div className="flex items-center gap-3 text-[10px] text-gray-500 mt-1">
        <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-emerald-500" />자사</span>
        <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-slate-400" />경쟁사</span>
        <span className="text-gray-400">우상단=불만 집중 · 우하단=관심↑·불만↓</span>
      </div>
    </div>
  );
}

/* 자사 관점 분포 카드 — 좌: 관점 파이 + 대표 관점(최다·긍정우세·부정우세) 긍/중립/부정 막대, 우: LLM 분석.
   선정(상위 N) 항목과 전략 예외(자사 단독) 항목이 동일 레이아웃을 공유(일관성). badge·note 로 예외 표식 주입. */
function OwnPerspectiveCard({ sa, label, ownPsp, analysis, badge = null, note = null, flagTag = null }) {
  const entries = Object.entries(ownPsp || {})
    .map(([p, v]) => ({ p, pos: v.pos || 0, neg: v.neg || 0, neu: v.neu || 0, tot: (v.pos || 0) + (v.neg || 0) + (v.neu || 0) }))
    .filter(e => e.tot > 0)
    .sort((a, b) => b.tot - a.tot);
  const totAll = entries.reduce((s, e) => s + e.tot, 0);
  const header = (
    <p className="text-sm font-bold text-gray-800 mb-2">
      {label ?? sa}{badge}
      <span className="ml-2 text-xs font-normal text-gray-400">· 자사 의견 {totAll}건</span>
      {flagTag}
    </p>
  );
  if (entries.length === 0) {
    return (<div className="text-left">{header}<p className="text-xs text-gray-400">자사 데이터 없음</p></div>);
  }
  const topShare = entries[0];
  const mostPos = [...entries].sort((a, b) => (b.pos / b.tot) - (a.pos / a.tot))[0];
  const mostNeg = [...entries].sort((a, b) => (b.neg / b.tot) - (a.neg / a.tot))[0];
  const repMap = new Map();
  [['최다 비율', topShare], ['긍정 우세', mostPos], ['부정 우세', mostNeg]].forEach(([tag, e]) => {
    if (repMap.has(e.p)) repMap.get(e.p).tags.push(tag);
    else repMap.set(e.p, { e, tags: [tag] });
  });
  return (
    <div className="text-left">
      {header}
      {note}
      <div className="flex flex-wrap items-stretch gap-6">
        {/* 좌: 관점 파이 + 대표 관점 긍/중립/부정. 고정 폭(파이 블록)으로 카드 간 우측 박스 x축 정렬. */}
        <div className="shrink-0 space-y-3 w-[240px]">
          <PspPie entries={entries} total={totAll} />
          <div className="space-y-1.5">
            {[...repMap.values()].map(({ e, tags }) => (
              <div key={e.p}>
                <p className="text-[11px] text-gray-500 mb-0.5">
                  <span className="font-semibold text-gray-700">{pspKo(e.p)}</span>
                  {' · '}{tags.join('·')} · {e.tot}건
                </p>
                <PolarityBar pos={e.pos} neu={e.neu} neg={e.neg} />
              </div>
            ))}
          </div>
        </div>
        {/* 우: LLM 분석(구조화 요약 보고). 좌측 전체 높이에 맞춰 stretch. */}
        <div className="flex-1 min-w-[360px] flex">
          <div className="w-full rounded-xl border border-gray-200 p-3.5 text-xs">
            <AnalysisView data={analysis} />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ReactionInsightReport({ report, candidateNames = {}, ownProductId = '' }) {
  const [showReliability, setShowReliability] = useState(false);
  // 선택: { aspect } = 항목 제목 클릭(전 상품) / { aspect, candidate } = 셀 클릭(해당 상품)
  const [sel, setSel] = useState(null);

  // 완성도 점수·루브릭은 웹에 표시하지 않고 콘솔에만 출력 (비교 매트릭스와 동일).
  useEffect(() => {
    if (report) logRubric(report.evaluation_score ?? 0);
  }, [report]);

  if (!report) return null;

  const content  = report.content ?? {};
  const matrix   = content.aspect_matrix ?? {};
  const labels   = content.aspect_labels ?? {};
  const quotes   = content.top_quotes ?? [];
  const meta     = content.channel_meta ?? {};
  const warnings = report.warnings ?? [];
  const degraded = warnings.some(w => String(w).includes('degrade'));
  const scoreRationale = warnings.find(w => String(w).startsWith('score_rationale:'));
  const otherWarnings  = warnings.filter(w => w !== scoreRationale && !String(w).includes('degrade'));

  // 반응 항목(행) — 전체 unit 수(전 candidate 합) 내림차순 정렬.
  const aspectTotal = a => Object.values(matrix[a] ?? {}).reduce((s, c) => s + (c.tuple_count || 0), 0);
  const aspects    = Object.keys(matrix).sort((a, b) => aspectTotal(b) - aspectTotal(a));
  // candidate 정렬 — comparison_matrix 와 동일: 자사 첫 번째, 이후 사전순 (item 1)
  const rawCids    = [...new Set(aspects.flatMap(a => Object.keys(matrix[a])))];
  const candidates = [
    ...(rawCids.includes(ownProductId) ? [ownProductId] : []),
    ...rawCids.filter(c => c !== ownProductId).sort(),
  ];

  const candidateOrder = Object.fromEntries(candidates.map((c, i) => [c, i]));
  const nameOf     = { ...candidateNames };
  // 친화명이 없는 후보는 내부 ID 접두사(comp_/func_/own_)를 떼고 표시
  candidates.forEach(cid => {
    if (!nameOf[cid]) nameOf[cid] = cid.replace(/^(comp_|func_|own_)/, '');
  });
  const labelOf    = (a) => labels[a] ?? a;

  // 상단 분포 차트용 자사(own) 분포 — 반응 항목: 히트맵 matrix 의 자사 tuple_count,
  // 관점: perspective_by_super_candidate 를 자사 기준으로 super_aspect 합산.
  const aspectOwnDist = Object.fromEntries(
    aspects.map(a => [a, matrix[a]?.[ownProductId]?.tuple_count || 0]));
  const perspectiveOwnDist = (() => {
    const acc = {};
    for (const cids of Object.values(content.perspective_by_super_candidate || {})) {
      const pm = cids?.[ownProductId];
      if (!pm) continue;
      for (const [p, v] of Object.entries(pm)) {
        acc[p] = (acc[p] || 0) + (v.pos || 0) + (v.neg || 0) + (v.neu || 0);
      }
    }
    return acc;
  })();

  // 대표 반응 — 미선택 시 접힘(빈 배열), 선택 시 해당 항목(+상품)만 필터
  const shownQuotes = !sel ? []
    : quotes.filter(q => q.aspect === sel.aspect
        && (!sel.candidate || q.candidate_id === sel.candidate));
  const sortByCandidate = qs => [...qs].sort((a, b) =>
    (candidateOrder[a.candidate_id] ?? 99) - (candidateOrder[b.candidate_id] ?? 99)
    || (b.intensity ?? 1) - (a.intensity ?? 1));
  const positiveQuotes = sortByCandidate(shownQuotes.filter(q => q.polarity === 'positive'));
  const negativeQuotes = sortByCandidate(shownQuotes.filter(q => q.polarity === 'negative'));
  // 셀 선택 = 단일 상품 / 항목명 선택 = 그 항목의 반응이 있는 상품들(candidate별 그룹)
  const selCids = !sel ? []
    : sel.candidate ? [sel.candidate]
    : candidates.filter(cid => shownQuotes.some(q => q.candidate_id === cid));

  // aspect 표시 순서 — 히트맵 행 순서 기준 (aspect 인사이트 정렬에 사용)
  const aspectOrder = Object.fromEntries(aspects.map((a, i) => [a, i]));

  // aspect 인사이트 (LLM 서술) — 히트맵 행 순서로 정렬, matrix 에 있는 aspect 만
  const aspectInsights = (content.aspect_insights ?? [])
    .filter(i => i.aspect in aspectOrder)
    .sort((a, b) => (aspectOrder[a.aspect] ?? 99) - (aspectOrder[b.aspect] ?? 99));

  // 히트맵 강조 플래그 (positive/negative 건수 기준):
  //   allNeg     = 데이터 있는 전 candidate 가 부정 → 행 전체 빨강 테두리
  //   ownOnlyNeg = 자사가 경쟁 평균 대비 부정률이 뚜렷이 높음(rate-margin) → 자사 셀 빨강 링
  //   ownOnlyPos = 자사가 경쟁 평균 대비 긍정률이 뚜렷이 높음 → 자사 셀 초록 링
  // OO_* 는 백엔드 REACTION_OWNONLY_* 와 동일 값(세 곳 일관). 분모=tuple_count(중립 포함).
  const OO_MIN = 0.5, OO_MARGIN = 0.3, OO_MINS = 4;
  const cellRate = c => (c && c.tuple_count > 0)
    ? { neg: (c.negative || 0) / c.tuple_count, pos: (c.positive || 0) / c.tuple_count, tot: c.tuple_count }
    : null;
  const aspectFlags = Object.fromEntries(aspects.map(a => {
    const withData = candidates
      .map(cid => cellPolarity(matrix[a]?.[cid]))
      .filter(p => p !== null);
    const allNeg = withData.length > 0 && withData.every(p => p === 'neg');
    const ow = cellRate(matrix[a]?.[ownProductId]);
    // 경쟁 '최고'값과 비교(평균은 강한 경쟁사를 희석). 표본 OO_MINS 미만 경쟁사는 제외(왜곡 방지).
    const comps = candidates.filter(c => c !== ownProductId)
      .map(c => cellRate(matrix[a]?.[c])).filter(c => c && c.tot >= OO_MINS);
    let ownOnlyNeg = false, ownOnlyPos = false;
    if (ow && ow.tot >= OO_MINS && comps.length) {
      const cNeg = Math.max(...comps.map(c => c.neg));
      const cPos = Math.max(...comps.map(c => c.pos));
      ownOnlyNeg = ow.neg >= OO_MIN && (ow.neg - cNeg) >= OO_MARGIN;
      ownOnlyPos = ow.pos >= OO_MIN && (ow.pos - cPos) >= OO_MARGIN;
    }
    return [a, { allNeg, ownOnlyNeg, ownOnlyPos }];
  }));
  const lastCid = candidates[candidates.length - 1];

  // 두괄식 — 항목별 자사 상대 위치(자사 감성 vs 경쟁 평균) · 반응량×격차 중요도순
  const takeaways = aspects.map(a => {
    const own = matrix[a]?.[ownProductId];
    if (!own || own.weighted_sentiment == null || !(own.tuple_count > 0)) return null;
    const comps = candidates
      .filter(c => c !== ownProductId)
      .map(c => matrix[a]?.[c]?.weighted_sentiment)
      .filter(s => s != null);
    const compAvg = comps.length ? comps.reduce((x, y) => x + y, 0) / comps.length : null;
    const gap = compAvg == null ? 0 : own.weighted_sentiment - compAvg;
    const zone = gap >= SENTIMENT_GAP ? 'win' : gap <= -SENTIMENT_GAP ? 'lose' : 'bat';
    return { aspect: a, zone, importance: own.tuple_count * (Math.abs(gap) || 0.01) };
  }).filter(Boolean).sort((x, y) => y.importance - x.importance);

  return (
    <div className="mb-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
      {/* ── 1. 헤더 ── */}
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <h3 className="text-lg font-bold text-gray-900">💬 {content.title ?? '고객 반응 인사이트'}</h3>
        {degraded && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 font-medium">
            degraded — LLM 서술 생략 (집계만 제공)
          </span>
        )}
      </div>
      <p className="text-xs text-gray-400 mb-4">
        {report.rubric_version} · 생성 {String(report.generated_at ?? '').slice(0, 19).replace('T', ' ')}
      </p>

      {/* ── 2. 표본 메타 — candidate별 채널 상세(유튜브 영상/의견/질문 · 커뮤니티 게시글/의견/질문) ── */}
      {content.totals && (
        <p className="text-[11px] text-gray-500 mb-1.5">
          전체 의견 <b>{content.totals.units}</b> · 질문 <b>{content.totals.questions}</b>
          {' · '}히트맵 반영(자사+경쟁) <b>{content.totals.candidate_units}</b>
          {' · '}제외(비트래블카드·기타·미귀속) <b>{content.totals.excluded_units}</b>
        </p>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-2 text-[11px] text-gray-500">
        {candidates.filter(cid => meta[cid]).map(cid => {
          const yt = meta[cid]?.youtube ?? {};
          const comm = meta[cid]?.community ?? {};
          const hasDetail = meta[cid]?.youtube != null || meta[cid]?.community != null;
          const ytHas = (yt.units ?? 0) > 0 || (yt.questions ?? 0) > 0;
          const commHas = (comm.units ?? 0) > 0 || (comm.questions ?? 0) > 0;
          const cc = meta[cid]?.channel_counts ?? {};
          const sampleSize = meta[cid]?.sample_size ?? 0;
          return (
            <div key={cid} className="px-2.5 py-1.5 rounded-lg bg-gray-50 border border-gray-100">
              <b className="text-gray-700">{nameOf[cid] ?? cid}</b>
              <div className="mt-0.5 space-y-0.5">
                {hasDetail ? (
                  <>
                    {ytHas && (
                      <div>유튜브 · 영상 {yt.videos ?? 0}개 · 의견 {yt.units ?? 0} · 질문 {yt.questions ?? 0}</div>
                    )}
                    {commHas && (
                      <div>커뮤니티 · 게시글 {comm.posts ?? 0}개 · 의견 {comm.units ?? 0} · 질문 {comm.questions ?? 0}</div>
                    )}
                    {!ytHas && !commHas && <div className="text-gray-400">표본 없음</div>}
                  </>
                ) : (
                  // 구 envelope 폴백(백엔드 재시작 전) — 상세 필드가 없으면 총합만 표시
                  <div>
                    표본 {sampleSize}건
                    {(cc.youtube || cc.community) ? ` (YT ${cc.youtube ?? 0} · 커뮤니티 ${cc.community ?? 0})` : ''}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* ── 한눈 요약 (히트맵 위) — A: 고객 관심(질문·LLM) / B: 고객 평가(의견·결정론) ── */}
      {(() => {
        const qo = content.question_overview || {};
        // B블록(결정론): 강점=win·약점=lose(반응량×격차 상위), 기회=전 candidate 부정 우세.
        const strength = takeaways.filter(t => t.zone === 'win').slice(0, 3).map(t => labelOf(t.aspect));
        const weakness = takeaways.filter(t => t.zone === 'lose').slice(0, 3).map(t => labelOf(t.aspect));
        const opportunity = aspects.filter(a => {
          if (a === '종합' || a === '기타') return false;
          const oc = matrix[a]?.[ownProductId];
          if (!oc || oc.weighted_sentiment == null || !(oc.tuple_count > 0) || oc.weighted_sentiment >= 0) return false;
          const cs = candidates.filter(c => c !== ownProductId)
            .map(c => matrix[a]?.[c]).filter(c => c && c.weighted_sentiment != null && c.tuple_count > 0);
          return cs.length > 0 && cs.every(c => c.weighted_sentiment < 0);
        }).slice(0, 3).map(labelOf);
        const hasA = (qo.focus || []).length || (qo.common || []).length || (qo.diff || []).length;
        const hasB = strength.length || weakness.length || opportunity.length;
        if (!hasA && !hasB) return null;
        const QRow = ({ label, items }) => (items && items.length) ? (
          <div className="flex gap-2"><span className="w-8 shrink-0 font-bold text-gray-500">{label}</span>
            <span className="text-gray-700">{items.join(' ')}</span></div>
        ) : null;
        const BRow = ({ label, items, color }) => items.length > 0 ? (
          <div className="flex gap-2"><span className={`w-8 shrink-0 font-bold ${color}`}>{label}</span>
            <span className="text-gray-700">{items.join(' / ')}</span></div>
        ) : null;
        return (
          <div className="mb-4 rounded-xl border border-indigo-100 bg-indigo-50/40 p-3.5 text-xs">
            <p className="font-bold text-indigo-700 mb-2">📌 한눈 요약</p>
            {hasA && (
              <div className="mb-2.5">
                <p className="text-[11px] font-semibold text-gray-500 mb-1">고객 관심 · 질문 기준</p>
                <div className="space-y-1">
                  <QRow label="집중" items={qo.focus} />
                  <QRow label="공통" items={qo.common} />
                  <QRow label="차이" items={qo.diff} />
                </div>
              </div>
            )}
            {hasB && (
              <div>
                <p className="text-[11px] font-semibold text-gray-500 mb-1">고객 평가 · 의견 기준</p>
                <div className="space-y-1">
                  <BRow label="강점" items={strength} color="text-green-700" />
                  <BRow label="약점" items={weakness} color="text-red-600" />
                  <BRow label="기회" items={opportunity} color="text-amber-600" />
                </div>
                <p className="mt-1.5 text-[10px] text-gray-400">
                  강점·약점 = 자사 감성이 경쟁 평균 대비 ±{SENTIMENT_GAP} 이상 · 기회 = 전 candidate 부정 우세(선점 여지)
                </p>
              </div>
            )}
          </div>
        );
      })()}

      {/* ── 반응 항목 설명 (sub_cluster 근거 LLM 한 문장) — 한눈 요약 하단 ── */}
      {content.aspect_descriptions && Object.keys(content.aspect_descriptions).length > 0 && (
        <div className="mb-3 rounded-xl bg-gray-50 border border-gray-100 p-3">
          <p className="text-[11px] font-semibold text-gray-500 mb-1.5">반응 항목 설명</p>
          <div className="space-y-1">
            {aspects.filter(a => content.aspect_descriptions[a]).map(a => (
              <p key={a} className="text-[11px] text-gray-600 leading-snug">
                <span className="font-semibold text-gray-800">{a}</span>
                {' — '}{content.aspect_descriptions[a]}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* ── 전체 반응 성향: 반응 항목·관점 분포 (전체 candidate 종합 + 자사 분포 겹침, 대표 강조).
             질문 유형 분포는 unit 분석 단계 이후 다룰 예정이라 보류. ── */}
      {(aspects.length > 0 || content.perspective_overall) && (
        <div className="mb-5 flex flex-wrap gap-x-8 gap-y-4">
          <DistBar
            title="반응 항목(super_aspect) 분포"
            subtitle={`전체 종합 vs 자사 · ★=전체 대표`}
            dist={Object.fromEntries(aspects.map(a => [a, aspectTotal(a)]))}
            ownDist={aspectOwnDist}
            labelOf={a => labels[a] ?? a}
          />
          <DistBar
            title="관점(perspective) 분포"
            subtitle={`전체 종합 vs 자사 · ★=전체 대표`}
            dist={content.perspective_overall}
            ownDist={perspectiveOwnDist}
            labelOf={pspKo}
          />
        </div>
      )}

      {/* ── 3. aspect × candidate 히트맵 ── */}
      <h4 className="text-sm font-bold text-gray-800 mb-1">고객 반응 히트맵</h4>
      {/* 1줄: 설명 */}
      <p className="text-xs text-gray-600 mb-1">
        <b>항목명</b>을 클릭하면 그 항목의 전 상품 대표 반응, <b>셀</b>을 클릭하면 그 상품·항목의 긍/부정 대표 반응(각 최대 3건)을 아래에 펼쳐 봅니다.
      </p>
      {/* 2줄: 범례 우측 정렬 — 테두리 3종 → 감성 그라데이션 순 */}
      <div className="flex flex-wrap justify-end items-center gap-x-3 gap-y-1 mb-1 text-[11px] text-gray-500">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-sm border-2 border-amber-400" />
          전 상품 공통 부정
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-sm border-2 border-red-500" />
          자사만 부정
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-sm border-2 border-green-500" />
          자사만 긍정
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="text-green-600 font-medium">긍정</span>
          <span className="inline-block h-2.5 w-20 rounded-full"
            style={{ background: 'linear-gradient(to right, rgb(34,197,94), #e5e7eb, rgb(239,68,68))' }} />
          <span className="text-red-600 font-medium">부정</span>
        </span>
      </div>
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
            {aspects.map(a => {
              const flags = aspectFlags[a] ?? {};
              // 행 강조 우선순위: 전체부정 → 자사만부정 → 자사만긍정 (상호배타 — 겹침 없음)
              const markKey = flags.allNeg ? 'allNeg'
                : flags.ownOnlyNeg ? 'ownNeg'
                : flags.ownOnlyPos ? 'ownPos' : null;
              const mark = markKey ? ROW_MARK[markKey] : null;
              const aspectSelected = sel?.aspect === a && !sel?.candidate;   // 제목 클릭 상태
              return (
                <tr key={a} className="hover:bg-gray-50">
                  <td
                    onClick={() => setSel(aspectSelected ? null : { aspect: a })}
                    title="이 항목의 전 상품 대표 반응 보기"
                    className={`px-3 py-2 text-xs font-semibold text-gray-800 break-keep cursor-pointer ${mark ? mark.label : 'border-b border-gray-100'} ${aspectSelected ? 'bg-indigo-50' : ''}`}>
                    {labelOf(a)}
                    {mark && <span className={`block text-[10px] font-normal ${mark.textCls}`}>{mark.text}</span>}
                    {aspectSelected && <span className="block text-[10px] text-indigo-500 font-normal">▼ 전 상품 대표 반응</span>}
                  </td>
                  {candidates.map(cid => {
                    const cell = matrix[a]?.[cid];
                    const cellSelected = sel?.aspect === a && sel?.candidate === cid;
                    const borders = mark
                      ? `${mark.cell} ${cid === lastCid ? mark.last : ''}`
                      : 'border-b border-l border-gray-100';
                    return (
                      <td key={cid}
                        onClick={() => cell && setSel(cellSelected ? null : { aspect: a, candidate: cid })}
                        title={cell ? '이 상품·이 항목의 긍·부정 대표 반응 보기' : ''}
                        className={`px-3 py-2 text-center ${borders} ${cell ? 'cursor-pointer' : ''} ${cellSelected ? 'ring-2 ring-inset ring-indigo-400' : ''}`}
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
              );
            })}
          </tbody>
        </table>
      </div>

      {/* ── 자사 관점 분포 (의견 상위 누적 50% 항목) — 항목별 파이 + 대표 관점 긍/중립/부정 ── */}
      {(() => {
        const pspBySC = content.perspective_by_super_candidate || {};
        const own = ownProductId;
        // 항목별 총 의견량(분석 대상 candidate 전체, 긍+중+부). 종합·기타는 선정에서 제외.
        const vol = {};
        for (const [sa, cids] of Object.entries(pspBySC)) {
          if (sa === '종합' || sa === '기타') continue;
          let t = 0;
          for (const pm of Object.values(cids)) for (const v of Object.values(pm)) t += (v.pos || 0) + (v.neg || 0) + (v.neu || 0);
          vol[sa] = t;
        }
        const grand = Object.values(vol).reduce((s, n) => s + n, 0);
        if (grand === 0) return null;
        // 백엔드 selected_aspects(분석과 동일 기준) 우선, 없으면 프런트 누적 50% 계산.
        const selected = (content.selected_aspects && content.selected_aspects.length)
          ? content.selected_aspects
          : (() => {
              const r = Object.entries(vol).sort((a, b) => b[1] - a[1]);
              const s = []; let a = 0;
              for (const [sa, v] of r) { s.push(sa); a += v; if (a >= grand * 0.5) break; }
              return s;
            })();
        // 결정론 요약(무 LLM): 상위 항목별 자사 net 감성 vs 경쟁 평균 격차로 강점/약점/기회 분류.
        const GAP = SENTIMENT_GAP;
        const sum = { win: [], lose: [], opp: [] };
        let posDom = 0, negDom = 0;
        for (const sa of selected) {
          const oc = matrix[sa]?.[own];
          if (!oc || oc.weighted_sentiment == null || !(oc.tuple_count > 0)) continue;
          const cs = candidates.filter(c => c !== own)
            .map(c => matrix[sa]?.[c]?.weighted_sentiment).filter(s => s != null);
          const cAvg = cs.length ? cs.reduce((x, y) => x + y, 0) / cs.length : 0;
          const gap = oc.weighted_sentiment - cAvg;
          if (oc.weighted_sentiment >= 0) posDom++; else negDom++;
          if (gap >= GAP) sum.win.push(sa);
          else if (gap <= -GAP) sum.lose.push(sa);
          else if (oc.weighted_sentiment < 0 && cAvg < 0) sum.opp.push(sa);
        }
        const SumRow = ({ label, color, items, note }) => items.length > 0 ? (
          <div className="flex gap-2">
            <span className={`w-10 shrink-0 font-bold ${color}`}>{label}</span>
            <span className="text-gray-700">{items.map(labelOf).join(', ')}
              <span className="text-gray-400"> · {note}</span></span>
          </div>
        ) : null;
        return (
          <div className="mb-6">
            <div className="flex items-baseline gap-2 mb-2 flex-wrap">
              <h4 className="text-sm font-bold text-gray-800">고객 반응 집중 항목 — 자사 관점 분석</h4>
              <span className="text-[11px] text-gray-400">의견량 누적 50% 상위 항목(종합·기타 제외)</span>
            </div>
            {/* 결정론 요약 박스 — 한눈에 자사 인식(강점·약점·기회). 데이터 기반, 서술 아님. */}
            <div className="mb-4 rounded-xl border border-gray-200 bg-gray-50/60 p-3 text-xs">
              <p className="font-bold text-gray-700 mb-2">자사 인식 요약
                <span className="font-normal text-gray-400"> · 상위 {selected.length}개 항목 기준</span></p>
              <div className="space-y-1.5">
                <div className="flex gap-2">
                  <span className="w-10 shrink-0 font-bold text-gray-500">종합</span>
                  <span className="text-gray-700">긍정 우세 {posDom}개 · 부정 우세 {negDom}개</span>
                </div>
                <SumRow label="강점" color="text-green-700" items={sum.win} note="경쟁 평균 대비 자사 긍정 우위" />
                <SumRow label="약점" color="text-red-600" items={sum.lose} note="경쟁 평균 대비 자사 부정 취약" />
                <SumRow label="기회" color="text-amber-600" items={sum.opp} note="자사·경쟁 공통 부정 — 선점 여지" />
              </div>
            </div>
            <div className="space-y-6">
              {selected.map(sa => {
                const f = aspectFlags[sa] || {};
                const flagTag = f.ownOnlyNeg
                  ? <span className="ml-2 text-[11px] font-bold px-1.5 py-0.5 rounded bg-red-50 text-red-600">자사 단독 부정</span>
                  : f.ownOnlyPos
                    ? <span className="ml-2 text-[11px] font-bold px-1.5 py-0.5 rounded bg-green-50 text-green-700">자사 단독 긍정</span>
                    : null;
                return (
                  <OwnPerspectiveCard key={sa} sa={sa} label={labelOf(sa)}
                    ownPsp={(pspBySC[sa] || {})[own]} analysis={content.aspect_analysis?.[sa]} flagTag={flagTag} />
                );
              })}
            </div>
          </div>
        );
      })()}

      {/* ── 전략적 관심(저빈도) 예외 항목 — 자사 단독 부정/긍정 (상위 N 밖). 임시 휴리스틱. ── */}
      {(() => {
        const own = ownProductId;
        const pol = content.aspect_candidate_polarity || {};
        const pspBySC = content.perspective_by_super_candidate || {};
        const negList = content.own_only_negative_aspects || [];
        const posList = content.own_only_positive_aspects || [];
        if (negList.length === 0 && posList.length === 0) return null;
        const card = (sa, kind) => {
          const ownT = (pol[sa] || {})[own] || { pos: 0, neu: 0, neg: 0 };
          const tot = ownT.pos + ownT.neu + ownT.neg;
          const rate = tot ? Math.round((kind === 'neg' ? ownT.neg : ownT.pos) / tot * 100) : 0;
          // 태그를 '자사 의견 n건' 우측(flagTag)으로 이동 — 별도 소제목 헤더 대신 카드 인라인 표식.
          const flagTag = (
            <span className={`ml-2 text-[11px] font-bold px-1.5 py-0.5 rounded ${kind === 'neg' ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-700'}`}>
              {kind === 'neg' ? '⚠ 자사 단독 부정' : '★ 자사 단독 긍정'}
            </span>
          );
          const note = (
            <p className="text-[11px] text-gray-500 leading-snug mb-2">
              자사 {kind === 'neg' ? '부정률' : '긍정률'} {rate}% (자사 +{ownT.pos}/−{ownT.neg})
              {' — 경쟁 최고 대비 뚜렷이 높음'}
            </p>
          );
          return (
            <OwnPerspectiveCard key={sa} sa={sa} label={labelOf(sa)}
              ownPsp={(pspBySC[sa] || {})[own]} analysis={content.aspect_analysis?.[sa]}
              note={note} flagTag={flagTag} />
          );
        };
        // 제목·설명 제거 — 카드의 인라인 태그가 성격을 표시. 부정 → 긍정 순.
        return (
          <div className="mb-6 space-y-6">
            {negList.map(sa => card(sa, 'neg'))}
            {posList.map(sa => card(sa, 'pos'))}
          </div>
        );
      })()}

      {/* ── 4. 항목별 고객 반응 분석 (LLM 서술) — 히트맵과 추이 사이 해석 층 ── */}
      {aspectInsights.length > 0 && (
        <div className="mb-5">
          <h4 className="text-sm font-bold text-gray-800 mb-2">항목별 고객 반응 분석</h4>
          <div className="space-y-3">
            {aspectInsights.map(ins => (
              <div key={ins.aspect}
                className="rounded-xl border border-gray-100 bg-gray-50/60 p-3">
                <button type="button"
                  onClick={() => setSel({ aspect: ins.aspect })}
                  className="text-xs font-semibold text-indigo-700 hover:underline mb-1">
                  {labelOf(ins.aspect)} 대표 반응 보기 ▾
                </button>
                {ins.headline && (
                  <p className="text-sm font-bold text-gray-900 mb-1">{ins.headline}</p>
                )}
                {ins.narrative && (
                  <p className="text-xs leading-relaxed text-gray-700">{ins.narrative}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 대표 반응 (히트맵 직하단) — 미선택 시 접힘, 셀/항목명 클릭 시 펼침 ── */}
      {sel && (
        <div className="mb-5">
          <h4 className="text-sm font-bold text-gray-800 mb-2">
            {sel.candidate ? `${nameOf[sel.candidate] ?? sel.candidate} ` : ''}{labelOf(sel.aspect)} 대표 반응
            <button type="button" onClick={() => setSel(null)}
              className="ml-2 text-xs font-normal text-indigo-600 underline">접기</button>
          </h4>
          <div className="space-y-4">
            {selCids.map(cid => {
              const pos = positiveQuotes.filter(q => q.candidate_id === cid);
              const neg = negativeQuotes.filter(q => q.candidate_id === cid);
              return (
                <div key={cid} className={!sel.candidate ? 'rounded-xl border border-gray-100 p-3' : ''}>
                  {!sel.candidate && (
                    <p className="text-xs font-bold text-gray-700 mb-2">{nameOf[cid] ?? cid}</p>
                  )}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <p className="text-xs font-semibold text-green-700 mb-2">👍 긍정 반응 ({pos.length})</p>
                      <div className="space-y-3">
                        {pos.map((q, i) => <QuoteCard key={i} q={q} nameOf={nameOf} />)}
                        {pos.length === 0 && (
                          <p className="text-xs text-gray-400">표시할 긍정 반응이 없습니다.</p>
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-red-700 mb-2">👎 부정 반응 ({neg.length})</p>
                      <div className="space-y-3">
                        {neg.map((q, i) => <QuoteCard key={i} q={q} nameOf={nameOf} />)}
                        {neg.length === 0 && (
                          <p className="text-xs text-gray-400">표시할 부정 반응이 없습니다.</p>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 월간 고객 반응 추이·개선 제안 섹션은 신 파이프라인 리포트에서 제거(2026-08).
          추이는 posted_at 부재로 비활성이었고, 제안은 신 파이프라인 미산출이라 함께 삭제. */}

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

      {/* ── 질문 유형 — 자사 관점 분석(최하단). 상: 좌=자사 질문의 반응 항목 파이 / 우=질문 유형 전체 vs 자사, 하: LLM. ── */}
      {content.question_type_overall && Object.keys(content.question_type_overall).length > 0 && (
        <div className="mt-6 pt-4 border-t border-gray-100">
          <h4 className="text-sm font-bold text-gray-800 mb-3">질문 유형 — 자사 관점 분석</h4>
          <div className="flex flex-wrap items-stretch gap-6 mb-5">
            {/* 좌: 자사 질문의 반응 항목(super_aspect) 분포 파이 — 긍부정 없음 */}
            {(() => {
              const entries = Object.entries(content.question_aspect_own || {})
                .map(([p, n]) => ({ p, tot: n })).filter(e => e.tot > 0)
                .sort((a, b) => b.tot - a.tot);
              const totAll = entries.reduce((s, e) => s + e.tot, 0);
              return (
                <div className="shrink-0 w-[300px]">
                  <p className="text-xs font-bold text-gray-700 mb-2">자사 질문 — 반응 항목 분포
                    <span className="font-normal text-gray-400"> · 자사 질문 {totAll}건</span></p>
                  {totAll > 0
                    ? <PspPie entries={entries} total={totAll} labelOf={labelOf} />
                    : <p className="text-xs text-gray-400">자사 질문 표본 없음</p>}
                </div>
              );
            })()}
            {/* 중: 질문 유형 분포(전체 vs 자사, 자사 바가 전체 바 위) */}
            <DistBar
              title="질문 유형(question_type) 분포"
              subtitle="전체 종합 vs 자사 · ★=전체 대표"
              dist={content.question_type_overall}
              ownDist={content.question_type_own}
              labelOf={qtKo}
              ownFirst
            />
            {/* 우: 질문량 × 의견 부정률 산점도(중요도×불확실성 분리) */}
            <QScatter qabc={content.question_aspect_by_candidate} matrix={matrix}
              own={ownProductId} candidates={candidates} nameOf={nameOf} />
          </div>
          {/* 하: LLM 분석(전체 폭) */}
          <div className="rounded-xl border border-gray-200 p-3.5 text-xs">
            <QuestionAnalysisView data={content.question_analysis} />
          </div>
        </div>
      )}
    </div>
  );
}
