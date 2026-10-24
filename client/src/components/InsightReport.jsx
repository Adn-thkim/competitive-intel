/**
 * InsightReport — 최종 인사이트 리포트(축약판) 렌더러.
 *
 * 입력: report_outputs["insight_report"] 엔벨로프 (server/graph/nodes/insight_report_node.py)
 *   content.external  : 외부 조사 뷰(이력·재무·시장, server/graph/insight/external_view.py) — 없으면 해당 블록 pending
 *   content.narrative : 종합 agent 서술(요약·위치·경쟁·반응 해석·기회·권고)
 *   content.tables    : 결정론 통계 표(server/graph/insight/stats.py)
 *   content.evidence  : 원문 분류 결과(건수는 코드가 확정)
 *   content.pending   : 외부 데이터가 필요해 축약판에서 빠진 블록(2단계)
 * 형식 참고: docs/mockups/domain_analysis_tab.html 의 pane-final2 · docs/mockups/build_final_tab.py
 * 작성 규칙: docs/mockups/insight_report_authoring_rules.md — 지수는 표 안에만, 서술은 건수로.
 */
import { useState } from 'react';

const VERDICT = {
  '우위': 'bg-green-100 text-green-700', '접전': 'bg-amber-100 text-amber-700',
  '열위': 'bg-red-100 text-red-700', '동률': 'bg-gray-100 text-gray-600',
  '판단 불가': 'bg-gray-100 text-gray-500', '표본 부족': 'bg-orange-100 text-orange-700',
  '측정 불가': 'bg-gray-100 text-gray-400',
};
const KIND = {
  '기회': 'bg-indigo-600 text-white', '선결 조건': 'bg-amber-500 text-white',
  '방어': 'bg-gray-700 text-white', '재분류': 'bg-gray-200 text-gray-700',
};
const COST = { '없음': 'text-gray-400', '낮음': 'text-green-700', '중간': 'text-amber-700', '높음': 'text-red-700' };
const CH = { youtube: '유튜브', community: '커뮤니티', blog: '블로그' };

const Badge = ({ text, cls }) => (
  <span className={`inline-block text-[10px] px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap ${cls}`}>{text}</span>
);
const Inference = ({ on }) => (on ? <Badge text="추론" cls="bg-blue-100 text-blue-700 ml-1 align-middle" /> : null);
const fmt = v => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : '±'}${Math.abs(v).toFixed(2)}`);

function Section({ n, id, title, lead, keyMsg, children }) {
  return (
    <section id={id} className="mb-5 bg-white rounded-2xl border border-gray-200 shadow-sm p-5 scroll-mt-4">
      <div className="flex flex-wrap items-baseline gap-2 mb-1">
        <span className="text-[11px] font-mono text-gray-300">{n}</span>
        <h3 className="text-lg font-bold text-gray-900">{title}</h3>
      </div>
      {lead && <p className="text-xs text-gray-500 mb-3">{lead}</p>}
      {keyMsg && (
        <div className="mb-4 rounded-lg border-l-4 border-indigo-500 bg-indigo-50/60 px-3 py-2">
          <span className="text-[10px] font-bold text-indigo-600 mr-2 align-middle">핵심</span>
          <span className="text-sm font-semibold text-gray-900"><Rich t={keyMsg} /></span>
        </div>
      )}
      {children}
    </section>
  );
}
const H = ({ children, tag }) => (
  <p className="text-[11px] font-semibold text-gray-500 mb-1.5 mt-4 first:mt-0">
    {children}{tag && <span className="ml-1 text-[10px] text-indigo-500 font-normal">· {tag}</span>}
  </p>
);
// ── 가독성 보조 ─────────────────────────────────────────────────────────────
// 서술 속 날짜·수치·극성을 강조 표시(서술 원문은 바꾸지 않는다). 날짜=남색, 긍정 n=초록, 부정 n=빨강, 수치=굵게.
const RICH_RE = /(\d{4}-\d{2}(?:-\d{2})?)|((?:긍정|긍) \d+)|((?:부정|부) \d+)|([+−-]?\d[\d,.]*\s?(?:%p|%|건|회|일|만 명|만명|명|만 좌|만좌|좌|만장|장|종|개|억 원|억원|조 원|조원|달러|배))/g;
function Rich({ t }) {
  if (!t) return null;
  const out = []; let last = 0, m, k = 0;
  const re = new RegExp(RICH_RE.source, 'g');
  while ((m = re.exec(t))) {
    if (m.index > last) out.push(t.slice(last, m.index));
    const cls = m[1] ? 'font-mono text-indigo-700' : m[2] ? 'font-semibold text-green-700'
      : m[3] ? 'font-semibold text-red-700' : 'font-semibold text-gray-900';
    out.push(<span key={k++} className={cls}>{m[0]}</span>);
    last = m.index + m[0].length;
  }
  if (last < t.length) out.push(t.slice(last));
  return <>{out}</>;
}
const firstSentence = t => (t || '').split(/(?<=[.])\s/)[0];
const Lead = ({ t }) => (t ? <p className="text-xs text-gray-600 mb-1.5"><Rich t={t} /></p> : null);
// 각 블록의 해석 — 이 상자만 이어 읽어도 리포트 흐름이 잡히도록 시각적으로 분리
const Take = ({ t }) => (t ? (
  <div className="flex gap-2 items-start mt-2 mb-3 rounded-lg bg-indigo-50 border border-indigo-100 px-3 py-2">
    <span className="text-[10px] font-bold text-white bg-indigo-500 rounded px-1.5 py-0.5 shrink-0 mt-px">의미</span>
    <p className="text-xs text-gray-900 leading-relaxed"><Rich t={t} /></p>
  </div>) : null);
// 긍/중/부 막대 — 건수 비율을 색으로
function PnnBar({ s, w = 110 }) {
  if (!s || !s.n) return <span className="text-gray-300">—</span>;
  const seg = [['pos', 'bg-green-500'], ['neu', 'bg-gray-300'], ['neg', 'bg-red-500']];
  return (
    <div className="flex items-center gap-2">
      <div className="flex h-2.5 rounded-full overflow-hidden bg-gray-100 shrink-0" style={{ width: w }}>
        {seg.map(([k, c]) => (s[k] ? <div key={k} className={c} style={{ width: `${(s[k] / s.n) * 100}%` }} /> : null))}
      </div>
      <span className="text-[11px] whitespace-nowrap tabular-nums">
        <span className="text-green-700 font-semibold">{s.pos}</span> · <span className="text-gray-400">{s.neu ?? 0}</span> · <span className="text-red-700 font-semibold">{s.neg}</span>
        <span className="text-gray-400"> / {s.n}</span>
      </span>
    </div>
  );
}
// 지수 셀 — 부호·크기를 색으로, 표본 5건 미만은 회색
function IdxCell({ s }) {
  if (!s?.n) return <span className="text-gray-300">—</span>;
  const v = s.index ?? 0;
  const tone = s.n < 5 ? 'bg-gray-50 text-gray-400' : v >= 0.5 ? 'bg-green-100 text-green-800' : v > 0.1 ? 'bg-green-50 text-green-700'
    : v <= -0.5 ? 'bg-red-100 text-red-800' : v < -0.1 ? 'bg-red-50 text-red-700' : 'bg-gray-50 text-gray-600';
  return <span className={`inline-block rounded px-1.5 py-0.5 tabular-nums whitespace-nowrap ${tone}`}>{fmt(s.index)} <span className="opacity-60">({s.n})</span></span>;
}
// 보조 근거 — 기본 접힘
function Fold({ title, children }) {
  return (
    <details className="mt-3 rounded-xl border border-gray-200 bg-gray-50/40 px-3 py-2">
      <summary className="cursor-pointer text-[11px] font-semibold text-gray-600">{title} <span className="font-normal text-gray-400">— 근거 표 펼치기</span></summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}
const VERDICT_EDGE = { '열위': 'border-l-red-400', '우위': 'border-l-green-500', '접전': 'border-l-amber-400' };
const KIND_EDGE = { '기회': 'border-l-indigo-500', '선결 조건': 'border-l-amber-500', '방어': 'border-l-gray-600', '재분류': 'border-l-gray-300' };
const Tile = ({ label, value, note, tone = 'gray' }) => {
  const c = { gray: 'border-gray-100', red: 'border-red-200 bg-red-50', green: 'border-green-200 bg-green-50', amber: 'border-amber-200 bg-amber-50' }[tone];
  return (
    <div className={`rounded-xl border ${c} p-3`}>
      <p className="text-[11px] text-gray-500">{label}</p>
      <p className="text-xl font-bold text-gray-900 mt-0.5">{value}</p>
      {note && <p className="text-[10px] text-gray-400">{note}</p>}
    </div>
  );
};
function Table({ head, rows, ownRow, edge }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-gray-200 mb-2">
      <table className="min-w-full text-left border-collapse">
        <thead><tr className="bg-gray-50">
          {head.map((h, i) => (
            <th key={i} className={`px-3 py-2 text-xs font-semibold text-gray-500 whitespace-nowrap ${i ? 'border-l border-gray-200' : 'sticky left-0 bg-gray-50 z-10'}`}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} className={`hover:bg-gray-50/50 ${ownRow?.(ri) ? 'bg-blue-50/50' : ''}`}>
              {r.map((c, ci) => (
                <td key={ci} className={`px-3 py-2 align-top text-xs border-b border-gray-100 ${ci ? 'border-l text-gray-700'
                  : `sticky left-0 bg-white z-10 font-semibold text-gray-800 ${edge?.(ri) ? `border-l-4 ${edge(ri)}` : ''}`}`}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
const Pending = ({ items }) => (items?.length ? (
  <div className="mt-3 rounded-xl border border-dashed border-gray-300 bg-gray-50 p-3">
    <p className="text-[11px] font-semibold text-gray-500 mb-1">2단계 — 외부 데이터 연결 후 채워질 블록</p>
    <ul className="text-xs text-gray-500 space-y-0.5">
      {items.map((p, i) => <li key={i}>· {p.block} <span className="text-gray-400">— {p.needs}</span></li>)}
    </ul>
  </div>
) : null);
const Quotes = ({ list }) => (list?.length ? (
  <ul className="mt-1 space-y-0.5">{list.map((q, i) => <li key={i} className="text-[11px] text-gray-600 italic">“{q}”</li>)}</ul>
) : null);
const pnn = s => (s ? `${s.n}건 — 긍 ${s.pos} · 부 ${s.neg} · 중 ${s.neu}` : '—');
const CHANGE = {
  '신설': 'bg-green-100 text-green-700', '확대': 'bg-green-100 text-green-700', '축소': 'bg-red-100 text-red-700',
  '원복': 'bg-blue-100 text-blue-700', '연장': 'bg-indigo-100 text-indigo-700', '종료': 'bg-gray-200 text-gray-700',
  '제휴': 'bg-amber-100 text-amber-700', '기타': 'bg-gray-100 text-gray-600',
};
const PHASE = { '종결': 'bg-gray-200 text-gray-600', '조건만 잔존': 'bg-gray-200 text-gray-600', '활성': 'bg-amber-200 text-amber-800' };
// 외부 조사 근거 표기 — 문서 id 를 원문 링크로
function Src({ ids, docs }) {
  const list = (Array.isArray(ids) ? ids : [ids]).filter(Boolean);
  if (!list.length) return null;
  return (
    <span className="ml-1 whitespace-nowrap">
      {list.map(id => (docs[id]
        ? <a key={id} href={docs[id].url} target="_blank" rel="noreferrer" title={`${docs[id].title} (${docs[id].published_at ?? ''})`}
             className="text-[9px] align-super text-indigo-500 hover:underline mr-0.5">[{id}]</a>
        : <span key={id} className="text-[9px] align-super text-gray-300 mr-0.5">[{id}]</span>))}
    </span>
  );
}

// ═════════════════════════════════════════════════════════════════════════
export default function InsightReport({ report, candidateNames = {}, ownProductId = '' }) {
  const [showLimits, setShowLimits] = useState(false);
  if (!report) return null;
  const c = report.content ?? {};
  const nv = c.narrative ?? {};
  const T = c.tables ?? {};
  const ev = c.evidence ?? {};
  const meta = c.meta ?? {};
  const own = ownProductId || meta.own_id;
  const cands = meta.candidates ?? [];
  const nm = id => (id === own ? '자사' : (candidateNames[id] ?? cands.find(x => x.candidate_id === id)?.name ?? id))
    .replace(/ \(자사\)$/, '');
  const pend = sec => (c.pending ?? []).filter(p => p.section.includes(sec));
  const X = c.external;                                   // 외부 조사 뷰 (없으면 해당 블록 pending)
  const docs = Object.fromEntries((X?.sources ?? []).map(d => [d.doc_id, d]));
  const S = ids => <Src ids={ids} docs={docs} />;
  const xh = X?.history, xf = X?.finance, xm = X?.market;
  const ownCounts = xh?.counts?.find(r => r.cid === own);
  const compReductions = (xh?.counts ?? []).filter(r => r.cid !== own).reduce((a, r) => a + (r.reductions ?? 0), 0);
  const genStr = JSON.stringify([c.generation ?? {}, X?.generation ?? {}]);
  const proxy = genStr.includes('session-proxy');
  const toc = [['s1', '1. 요약'], ['s2', '2. 시장 흐름'], ['s3', '3. 자사 위치'], ['s4', '4. 경쟁사 비교'],
               ['s5', '5. 고객 반응'], ['s6', '6. 기회'], ['s7', '7. 권고 및 결정 요청'], ['sA', '부록']];

  return (
    <div className="mb-6">
      {/* ── 헤더 ── */}
      <div className="mb-4 bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <h3 className="text-lg font-bold text-gray-900">📋 {(c.title ?? '최종 인사이트 리포트').replace(/\s*—\s*축약판$/, '')}</h3>
          {(c.pending ?? []).length > 0 && <Badge text="축약판" cls="bg-gray-900 text-white" />}
          {X && <Badge text={`외부 조사 ${X.as_of} 기준`} cls="bg-indigo-100 text-indigo-700" />}
          {proxy && <Badge text="LLM·수집 대행 작성본" cls="bg-amber-100 text-amber-800" />}
        </div>
        <p className="text-xs text-gray-400 mb-3">
          반응 {meta.units ?? '—'} unit · 분석 대상 {meta.candidate_units ?? '—'} · 질문 {meta.questions ?? '—'}건 ·
          자사 의견 {meta.own_units ?? '—'} · 질문 {meta.own_questions ?? '—'} · 생성 {String(report.generated_at ?? '').slice(0, 16).replace('T', ' ')} · {c.scope}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {toc.map(([id, t]) => (
            <a key={id} href={`#${id}`} className="text-[11px] px-2 py-1 rounded-lg border border-gray-200 text-gray-600 hover:border-gray-400">{t}</a>
          ))}
        </div>
      </div>

      {/* ── 1. 요약 ── */}
      <Section n="01" id="s1" title="요약" lead="결론과 결정 요청만 담았습니다. 근거는 3장 이하에 있습니다.">
        <div className="space-y-2 mb-4">
          {(nv.summary?.conclusions ?? []).map((k, i) => (
            <div key={i} className="flex gap-3 rounded-xl border border-gray-200 p-3">
              <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center">{i + 1}</span>
              <div>
                <p className="text-sm font-bold text-gray-900">{k.headline}<Inference on={k.is_inference} /></p>
                <p className="text-xs text-gray-700 mt-0.5 leading-relaxed"><Rich t={k.body} /></p>
              </div>
            </div>
          ))}
        </div>
        {X && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            {(() => { const t = xm?.tiles?.find(x => x.label === '1위 점유율'); return t
              ? <Tile label="해외 체크카드 1위 점유율" value={t.value} note={t.note} /> : <Tile label="시장 점유율" value="미확인" />; })()}
            <Tile label={`${(X.window_start ?? '').slice(0, 7)} 이후 혜택 축소`} value={`${ownCounts?.reductions ?? 0}회${ownCounts?.restorations ? ` + 원복 ${ownCounts.restorations}회` : ''}`}
              note={`경쟁 3사 합계 ${compReductions}회 — 수집 문서 기준`} tone={(ownCounts?.reductions ?? 0) > compReductions ? 'red' : 'gray'} />
            {xh?.upcoming?.[0]
              ? <Tile label="가장 가까운 만료" value={`${xh.upcoming[0].days_left}일 후`} note={`${xh.upcoming[0].name} · ${xh.upcoming[0].item}`} tone="amber" />
              : <Tile label="만료 임박" value="없음" />}
            <Tile label="반응 표본" value={`${meta.units} → ${meta.candidate_units}`} note="분석 대상 unit · 2채널 교차" />
          </div>
        )}
        <H>자사 포지션 한 장 요약 — 매트릭스 × 반응 결합</H>
        <Table head={['비교 축', '매트릭스', '고객 반응', '판정']}
          edge={i => VERDICT_EDGE[(nv.summary?.position_rows ?? [])[i]?.verdict]}
          rows={(nv.summary?.position_rows ?? []).map(r => [r.axis, <Rich t={r.matrix} />, <Rich t={r.reaction} />, <Badge text={r.verdict} cls={VERDICT[r.verdict] ?? VERDICT['판단 불가']} />])} />
        {(nv.recommendation?.decision_requests ?? []).length > 0 && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 mt-3">
            <p className="text-[11px] font-semibold text-amber-800 mb-1">결정 요청 {nv.recommendation.decision_requests.length}건 — 상세는 7장</p>
            <ol className="text-xs text-amber-900 space-y-0.5">
              {nv.recommendation.decision_requests.map((d, i) => <li key={i}><span className="font-mono text-amber-500 mr-1">{'①②③④⑤⑥⑦'[i]}</span>{d.title}</li>)}
            </ol>
          </div>
        )}
      </Section>

      {/* ── 2. 시장 흐름 (축약판 미포함) ── */}
      <Section n="02" id="s2" title="시장 흐름" lead="이 시장에서 무엇이 이미 끝났고 무엇이 지금 경쟁축인가."
        keyMsg={(() => { const a = xm?.axis_phases?.find(p => p.status === '활성'); return a ? `지금 경쟁축은 '${a.axis}'이다 — ${firstSentence(a.summary)}` : null; })()}>
        {xm?.tiles?.length > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            {xm.tiles.map((t, i) => <Tile key={i} label={t.label} value={<>{t.value}{S(t.doc_id)}</>} note={t.note} />)}
          </div>
        )}
        {xm?.axis_phases?.length > 0 && (<>
          <H tag="추론">경쟁축의 이동 — 무엇이 이미 끝났는가</H>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
            {xm.axis_phases.map((ph, i) => (
              <div key={i} className={`rounded-xl border p-3 ${ph.status === '활성' ? 'border-amber-200 bg-amber-50' : 'border-gray-200 bg-gray-50'}`}>
                <p className="text-[11px] font-bold text-gray-500 mb-1">{ph.period}</p>
                <p className="text-sm font-semibold text-gray-800 mb-1">{ph.axis}</p>
                <p className="text-xs text-gray-600 mb-2">{ph.summary}{S(ph.doc_ids)}</p>
                <Badge text={ph.status} cls={PHASE[ph.status] ?? PHASE['종결']} />
              </div>
            ))}
          </div>
        </>)}
        {xm?.shares?.length > 0 && (<>
          <H tag="상위 n사 합은 코드 계산">사업자별 점유율 — 기간·집계 범위별</H>
          <Table head={['기간', '집계 범위', '점유율', '상위 합']}
            rows={xm.shares.map(g => [g.period, g.scope,
              <span>{g.rows.map((r, i) => <span key={i} className="mr-2 whitespace-nowrap">{r.entity} {r.share.toFixed(2)}%{S(r.doc_id)}</span>)}</span>,
              Object.entries(g.cr).map(([k, v]) => `${k} ${v.toFixed(1)}%`).join(' · ') || '—'])} />
        </>)}
        {xm?.profitability?.length > 0 && (
          <div className="rounded-xl border border-gray-100 p-3 mb-3 mt-3">
            <p className="text-[11px] font-semibold text-gray-500 mb-1.5">업계 공통 수익성 압박</p>
            <ul className="text-xs text-gray-700 space-y-0.5">{xm.profitability.map((p, i) => <li key={i}>· {p.statement}{S(p.doc_id)}</li>)}</ul>
          </div>
        )}
        {(xh?.upcoming?.length > 0 || xm?.expirations?.length > 0) && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-3 mb-3">
            <p className="text-[11px] font-semibold text-red-800 mb-1">⏱ 만료 예정 — 기준일 {X.as_of}</p>
            <ul className="text-xs text-red-900 space-y-0.5">
              {(xh?.upcoming ?? []).map((u, i) => <li key={i}><b>{u.ends_at}</b> · {u.name} {u.item} <span className="text-red-700">({u.days_left}일 후)</span>{S(u.doc_ids)}</li>)}
            </ul>
            {xm?.expiry_clusters?.map((cl, i) => <p key={i} className="text-xs text-red-900 mt-1"><b>{cl.month}</b>에 {cl.n}건이 함께 만료된다 — {cl.items.join(', ')}</p>)}
          </div>
        )}
        {(xm?.conflicts?.length > 0 || xm?.notes?.length > 0) && (
          <details className="text-xs text-gray-600 mb-2">
            <summary className="cursor-pointer font-semibold text-gray-500">수치 대조 — 출처 간 차이 {xm.conflicts.length}건 · 성장률 재계산 {xm.growth.length}건</summary>
            <ul className="mt-1.5 space-y-0.5">
              {xm.conflicts.map((cf, i) => <li key={i}>⚑ {cf.entity} {cf.metric} {cf.period}: {cf.values.map(v => `${v.display}(${v.scope})`).join(' / ')}</li>)}
              {xm.growth.map((g, i) => <li key={`g${i}`}>✓ {g.entity} {g.prior_period}→{g.period} 계산 {g.computed_pct}% · 기재 {g.stated_pct ?? '—'}%{g.mismatch ? ' — 불일치' : ''}</li>)}
              {xm.share_check.map((g, i) => <li key={`s${i}`}>✓ {g.entity} {g.period} 이용액÷시장 {g.computed_pct}% · 기재 {g.stated_pct ?? '—'}%{g.mismatch ? ' — 불일치' : ''}</li>)}
              {xm.notes.map((n, i) => <li key={`n${i}`}>· {n}</li>)}
            </ul>
          </details>
        )}
        <Pending items={pend('2.')} />
      </Section>

      {/* ── 3. 자사 위치 ── */}
      <Section n="03" id="s3" title="자사 위치" lead="자사가 어떤 구조 위에 서 있고 무엇을 하고 있는가."
        keyMsg={firstSentence(nv.position?.bottleneck)}>
        {nv.position?.recovery_structure && (<>
          <H>회수 구조</H>
          <p className="text-sm text-gray-800">{nv.position.recovery_structure}<Inference on /></p>
        </>)}
        <H>관측되는 역할 — 상품이 실제로 무엇을 하고 있는가</H>
        <p className="text-sm text-gray-800 mb-2">{nv.position?.premise}<Inference on={nv.position?.premise_is_inference} /></p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="rounded-xl border border-green-200 bg-green-50/50 p-3">
            <p className="text-[11px] font-semibold text-green-700 mb-1">차별점 — 보유·관리 축</p>
            <ul className="text-xs text-gray-800 space-y-1">{(nv.position?.differentiators ?? []).map((s, i) => <li key={i}>＋ {s}</li>)}</ul>
          </div>
          <div className="rounded-xl border border-red-200 bg-red-50/50 p-3">
            <p className="text-[11px] font-semibold text-red-700 mb-1">열위 — 여행 중 사용 축</p>
            <ul className="text-xs text-gray-800 space-y-1">{(nv.position?.weaknesses ?? []).map((s, i) => <li key={i}>－ {s}</li>)}</ul>
          </div>
        </div>
        <H>병목의 위치</H>
        <p className="text-sm text-gray-800">{nv.position?.bottleneck}</p>
        {xh?.own_timeline?.length > 0 && (<>
          <H tag={`${xh.window?.[0]?.slice(0, 7)} ~ ${xh.window?.[1]?.slice(0, 7)}`}>
            자사 변경 이력 — {Object.entries(ownCounts?.counts ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ')}
          </H>
          <Table head={['시점', '기능', '변경', '회사가 밝힌 사유']}
            rows={xh.own_timeline.map(e => [e.date,
              e.feature,
              <span><Badge text={e.change_type} cls={CHANGE[e.change_type] ?? CHANGE['기타']} />{' '}
                {e.before && <span className="text-gray-400">{e.before} → </span>}{e.after}
                {e.ends_at && <span className="text-red-600"> (~{e.ends_at})</span>}{S(e.doc_ids)}</span>,
              e.stated_reason ? <span className="italic text-gray-600">“{e.stated_reason}”</span> : <span className="text-gray-300">—</span>])} />
        </>)}
        {xh?.statements?.some(s => s.cid === own) && (
          <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-3 mb-2">
            <p className="text-[11px] font-semibold text-indigo-700 mb-1">공식 입장 — 직접 인용</p>
            <ul className="text-xs text-gray-800 space-y-0.5">
              {xh.statements.filter(s => s.cid === own).map((s, i) => <li key={i}><span className="font-mono text-gray-400">{s.date}</span> {s.topic} — <i>“{s.quote}”</i>{S(s.doc_id)}</li>)}
            </ul>
            {xh.contradictions?.map((cd, i) => <p key={i} className="text-xs text-gray-800 mt-1"><b>모순</b> {cd.note}<Inference on /></p>)}
          </div>
        )}
        {xh?.notes?.length > 0 && <ul className="text-[11px] text-gray-500 mb-2 space-y-0.5">{xh.notes.map((n, i) => <li key={i}>· {n}</li>)}</ul>}
        {xf?.own?.length > 0 && (<>
          <H tag="공표 지표 · 증감은 코드 계산">획득·유지 지표</H>
          <Table head={['지표', '최근 값', '기준', '구간 증감']}
            rows={xf.own.map(sr => [sr.label,
              <span>{sr.points.at(-1).qualifier !== '정확' && <span className="text-gray-400">{sr.points.at(-1).qualifier} </span>}{sr.latest}{S(sr.points.map(p => p.doc_id))}</span>,
              `${sr.latest_as_of} (${sr.points.at(-1).as_of_kind})`,
              sr.steps.length ? sr.steps.map(st => `${st.from}→${st.to} ${st.delta_display}${st.per_month_display ? ` (월 ${st.per_month_display})` : ''}`).join(' / ') : '—'])} />
          {xf.unavailable?.length > 0 && <ul className="text-[11px] text-amber-700 space-y-0.5">{xf.unavailable.map((u, i) => <li key={i}>⚑ {u.metric} — {u.note}</li>)}</ul>}
        </>)}
        {nv.position?.combination_use_note && (<>
          <H>고객은 카드를 하나만 고르지 않는다</H>
          <p className="text-sm text-gray-800">{nv.position.combination_use_note}</p>
          <Quotes list={(ev.combination_use ?? []).map(x => `${x.quote}${x.partner ? ` — ${x.partner}` : ''}`)} />
        </>)}
        <Pending items={pend('3.')} />
      </Section>

      {/* ── 4. 경쟁사 비교 ── */}
      <Section n="04" id="s4" title="경쟁사 비교" lead="변별력 있는 축만. 4사 동일 항목은 생략했습니다."
        keyMsg={firstSentence(nv.competitor?.why_competitors_hold)}>
        <Lead t={nv.competitor?.lead} />
        <Table head={['비교 축', ...cands.map(x => (x.is_own ? `${x.name} (자사)` : x.name))]}
          rows={(T.matrix_features ?? []).filter(f => !f.identical).map(f => [
            <span>{f.label}{f.headline_only_identical && <span className="block mt-0.5"><Badge text="표기 동일 · 조건 상이" cls="bg-gray-100 text-gray-500" /></span>}</span>,
            ...cands.map(x => {
              const cell = f.cells?.[x.candidate_id] ?? {};
              return (
                <span>
                  {cell.items?.length ? cell.items.join(' · ') : (cell.headline || '미확인')}
                  {cell.condition && <span className="block text-[10px] text-gray-400 mt-0.5">{cell.condition}</span>}
                </span>
              );
            })])} />
        {xh?.structure_rows?.length > 0 && (<>
          <H tag="외부 조사 · 변동성은 코드 집계">사업자 구조</H>
          <Table head={['구조', ...cands.map(x => nm(x.candidate_id))]}
            rows={xh.structure_rows.map(r => [r.field, ...cands.map(x => {
              const v = r.cells?.[x.candidate_id] ?? {};
              return <span className={v.status === '미확인' ? 'text-gray-400' : ''}>{v.value ?? '미확인'}{S(r.computed ? [] : v.doc_ids)}</span>;
            })])} />
        </>)}
        {xh?.competitor_actions?.length > 0 && (<>
          <H tag="묶음 제목은 추론">경쟁사 최근 액션</H>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            {xh.competitor_actions.map((a, i) => (
              <div key={i} className="rounded-xl border border-gray-100 p-3">
                <p className="text-xs font-semibold text-gray-800 mb-1">{a.name} — {a.headline}<Inference on={a.is_inference} /></p>
                <p className="text-xs text-gray-600 mb-1">{a.detail}</p>
                <ul className="text-[11px] text-gray-500 space-y-0.5">
                  {a.events.map((e, j) => <li key={j}><span className="font-mono">{e.date}</span> <Badge text={e.change_type} cls={CHANGE[e.change_type] ?? CHANGE['기타']} /> {e.feature}{S(e.doc_ids)}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </>)}
        {xf?.competitors?.length > 0 && (
          <Fold title="경쟁사 규모 지표 — 공표 지표, 정의가 사업자마다 다름">
            <Table head={['상품', '지표', '값', '기준']}
              rows={xf.competitors.map(sr => [sr.name, sr.label, <span>{sr.latest}{S(sr.points.map(p => p.doc_id))}</span>, `${sr.latest_as_of} (${sr.points.at(-1).as_of_kind})`])} />
          </Fold>
        )}
        {nv.competitor?.why_competitors_hold && (<>
          <H>왜 경쟁사는 혜택을 줄이지 않는가</H>
          <p className="text-sm text-gray-800">{nv.competitor.why_competitors_hold}<Inference on /></p>
        </>)}
        <Pending items={pend('4.')} />
      </Section>

      {/* ── 5. 고객 반응 ── */}
      <Section n="05" id="s5" title="고객 반응" lead="시장이 실제로 어떻게 받아들였고 어디서 불편을 겪는가."
        keyMsg={firstSentence(nv.reaction?.aspect_table?.takeaway)}>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-4">
          <Tile label="수집 unit" value={meta.units} note="자사·경쟁 외 포함" />
          <Tile label="분석 대상" value={meta.candidate_units} note="버킷·미귀속 제외" />
          <Tile label="질문" value={meta.questions} note={`자사 대상 ${meta.own_questions}건`} />
          <Tile label="채널 2종 교차" value={Object.values(meta.channels ?? {}).join(' · ')} note={Object.keys(meta.channels ?? {}).join(' · ')} />
          <Tile label="자사 표본" value={meta.own_units} note="표본 크기 유의" tone="amber" />
        </div>

        <H tag="강도 가중 · 채널 무가중">항목별 자사 vs 경쟁 최고</H>
        <Lead t={nv.reaction?.aspect_table?.lead} />
        <p className="text-[10px] text-gray-400 mb-1"><span className="inline-block w-2 h-2 rounded-sm bg-green-500 mr-0.5" />긍정 · <span className="inline-block w-2 h-2 rounded-sm bg-gray-300 mr-0.5" />중립 · <span className="inline-block w-2 h-2 rounded-sm bg-red-500 mr-0.5" />부정 (건수) · 지수 칸 초록=우세 / 빨강=열세 / 회색=표본 5건 미만</p>
        <Table head={['항목', '자사 긍·중·부', '자사', '경쟁 최고', '판정']}
          edge={i => VERDICT_EDGE[(T.aspect_table ?? [])[i]?.verdict]}
          rows={(T.aspect_table ?? []).map(r => [
            r.aspect, <PnnBar s={r.own} />,
            r.own.n ? <IdxCell s={r.own} /> : '—',
            r.best ? <span className="whitespace-nowrap">{nm(r.best_competitor)} <IdxCell s={r.best} /></span> : '—',
            <Badge text={r.verdict} cls={VERDICT[r.verdict] ?? VERDICT['판단 불가']} />])} />
        <Take t={nv.reaction?.aspect_table?.takeaway} />

        <H>채널 분리 — 가중 대신 분할해서 본 것</H>
        <Lead t={nv.reaction?.channel_split?.lead} />
        <Table head={['상품', '검토 담론 (유튜브)', '사용 경험 담론 (커뮤니티)']}
          ownRow={i => cands[i]?.is_own}
          rows={cands.map(x => {
            const s = T.channel_split?.[x.candidate_id] ?? {};
            return [nm(x.candidate_id), <PnnBar s={s.youtube} />, <PnnBar s={s.community} />];
          })} />
        <Take t={nv.reaction?.channel_split?.takeaway} />

        <H tag="원문 분류">자사 부정 {T.own_negative_points?.n}건의 원인 분해</H>
        <Lead t={nv.reaction?.negative_causes?.lead} />
        <p className="text-[10px] text-gray-400 mb-1">왼쪽 띠 <span className="text-green-700 font-semibold">초록</span>=지출 없이 해결 가능 · <span className="text-red-700 font-semibold">빨강</span>=혜택 수준으로만 해결</p>
        <Table head={['원인', '건수 · 비중', '대표 원문', '해결 수단']}
          edge={i => ((ev.negative_causes ?? [])[i]?.solved_by_benefit ? 'border-l-red-400' : 'border-l-green-500')}
          rows={(ev.negative_causes ?? []).map(r => [r.label,
            <span className="flex items-center gap-1.5 whitespace-nowrap"><span className="inline-block h-2 rounded bg-red-400" style={{ width: `${Math.max(4, r.share)}px` }} /><b>{r.n}</b><span className="text-gray-400">{r.share}%</span></span>,
            <Quotes list={(r.quotes ?? []).slice(0, 2)} />,
            <span className={r.solved_by_benefit ? 'text-red-700' : 'text-green-700'}>{r.resolution}</span>])} />
        <Take t={nv.reaction?.negative_causes?.takeaway} />

        <H>질문 {T.questions?.total}건의 성격</H>
        <Lead t={nv.reaction?.questions?.lead} />
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-2">
          {Object.entries(T.questions?.groups ?? {}).map(([k, g]) => (
            <Tile key={k} label={g.label} value={`${g.n}건`}
              note={Object.entries(g.types).filter(([, v]) => v).map(([t, v]) => `${c.labels?.question_type?.[t] ?? t} ${v}`).join(' · ')}
              tone={k === 'info' ? 'amber' : 'gray'} />
          ))}
        </div>
        <Table head={['상품', '질문', '정보형', '자사 기준 도입 검토형']}
          rows={cands.map(x => {
            const s = T.questions?.by_candidate?.[x.candidate_id] ?? {};
            const ad = T.questions?.own_adoption_review;
            return [nm(x.candidate_id), s.n ?? 0, `${s.info ?? 0} / ${s.n ?? 0}`,
              x.is_own && ad ? `${ad.n} / ${ad.of} (원인·해결 ${ad.troubleshooting})` : '—'];
          })} />
        <Take t={nv.reaction?.questions?.takeaway} />

        {ev.focus_aspect && (<>
          <H tag="원문 분류">{ev.focus_aspect} — 가장 뒤진 항목의 분해</H>
          <Lead t={nv.reaction?.focus?.lead} />
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {[['부정 원인', ev.focus_negative_causes, r => (r.solved_by_benefit ? '한도·혜택으로 해결' : '남음')],
              ['긍정의 사용 상황', ev.focus_positive_contexts, () => ''],
              ['질문이 묻는 것', ev.focus_question_breakdown, r => (r.complains_about_level ? '수준 불만' : '수준 불만 아님')]]
              .map(([title, list, tag]) => (
                <div key={title} className="rounded-xl border border-gray-200 p-3">
                  <p className="text-[11px] font-semibold text-gray-600 mb-1">{title} ({(list ?? []).reduce((a, r) => a + r.n, 0)}건)</p>
                  {(list ?? []).map((r, i) => (
                    <div key={i} className="mb-1.5">
                      <p className="text-xs text-gray-800"><b>{r.label}</b> {r.n}건 {tag(r) && <span className="text-[10px] text-gray-400">· {tag(r)}</span>}</p>
                      <Quotes list={(r.quotes ?? []).slice(0, 1)} />
                    </div>
                  ))}
                </div>
              ))}
          </div>
          <Take t={nv.reaction?.focus?.takeaway} />
        </>)}

        <H tag="원문 분류">질문의 형태 — 유형 분류만으로는 보이지 않는 것</H>
        <Take t={nv.reaction?.question_shapes?.takeaway} />
        <Fold title={`질문 형태 ${(ev.question_shapes ?? []).length}종 — 원문과 의미`}>
          <Lead t={nv.reaction?.question_shapes?.lead} />
          <Table head={['형태', '건수', '원문', '무엇을 뜻하는가']}
            rows={(ev.question_shapes ?? []).map(r => [r.shape, r.n, <Quotes list={(r.quotes ?? []).slice(0, 2)} />, r.meaning])} />
          <p className="text-[10px] text-gray-400 mb-2">어법 패턴(“~아닌가요/맞나요/건가요”) 기계 집계 {T.question_patterns?.confirm_request?.n}건 — 하한값</p>
        </Fold>

        <H>강한 의견(강도 3)의 극성</H>
        <Take t={nv.reaction?.strong_opinions?.takeaway} />
        <Fold title="상품별 강한 의견 분포">
          <Lead t={nv.reaction?.strong_opinions?.lead} />
          <Table head={['상품', '긍정 · 부정']} ownRow={i => cands[i]?.is_own}
            rows={cands.map(x => { const s = T.strong_opinions?.[x.candidate_id] ?? {}; return [nm(x.candidate_id), <PnnBar s={{ ...s, neu: s.neu ?? Math.max(0, (s.n ?? 0) - (s.pos ?? 0) - (s.neg ?? 0)) }} />]; })} />
        </Fold>

        <div className="mt-3 pt-3 border-t border-gray-100">
          <button type="button" onClick={() => setShowLimits(v => !v)} className="text-xs font-semibold text-gray-500 hover:text-gray-700">
            표본 한계 — 해석 시 반드시 감안 {showLimits ? '▲ 접기' : '▼ 펼치기'}
          </button>
          {showLimits && <ul className="mt-2 text-xs text-gray-600 space-y-1">{(nv.reaction?.sample_limits ?? []).map((s, i) => <li key={i}>· {s}</li>)}</ul>}
        </div>
      </Section>

      {/* ── 6. 기회 ── */}
      <Section n="06" id="s6" title="기회" lead="경쟁사가 하지 않는 것 × 고객이 불편해하는 것 × 우리가 할 수 있는 것의 교집합에서만."
        keyMsg={firstSentence(nv.opportunity?.perspective_takeaway)}>
        <H>먼저 확정해야 할 사실</H>
        <ol className="text-sm text-gray-800 space-y-1 mb-2">
          {(nv.opportunity?.facts ?? []).map((f, i) => <li key={i} className="flex gap-2"><span className="font-mono text-gray-400">{i + 1}</span><span><Rich t={f} /></span></li>)}
        </ol>
        <H tag="강도 가중 · (건수)">관점별 자사 위치</H>
        <Table head={['관점', ...cands.map(x => nm(x.candidate_id)), '자사 위치']}
          rows={(T.perspective_table ?? []).map(p => [p.label,
            ...cands.map(x => <IdxCell s={p.by_candidate?.[x.candidate_id]} />),
            <span className={p.own_position?.includes('1위') ? 'font-bold text-green-700' : p.own_position === '최하' ? 'font-bold text-red-700' : ''}>{p.own_position}{p.own_sample_level === 'count_only' && <span className="ml-1"><Badge text="표본 부족" cls={VERDICT['표본 부족']} /></span>}</span>])} />
        <Take t={nv.opportunity?.perspective_takeaway} />

        <div className="space-y-3 mt-3">
          {(nv.opportunity?.items ?? []).map(o => (
            <div key={o.id} className={`rounded-xl border border-gray-200 border-l-4 ${KIND_EDGE[o.kind] ?? 'border-l-gray-300'} p-3.5`}>
              <div className="flex flex-wrap items-center gap-2 mb-1.5">
                <span className="font-mono text-xs text-gray-400">{o.id}</span>
                <p className="text-sm font-bold text-gray-900">{o.title}</p>
                <Badge text={o.kind} cls={KIND[o.kind] ?? KIND['재분류']} />
                {(o.action_refs ?? []).length > 0 && <span className="text-[10px] text-gray-400">→ {o.action_refs.join(' · ')}</span>}
              </div>
              {o.kind !== '재분류' && o.intersection && (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-2">
                  {[['경쟁 공백', o.intersection.competitor_gap], ['고객 신호', o.intersection.customer_signal], ['자사 역량', o.intersection.own_capability]].map(([k, v]) => (
                    <div key={k} className="rounded-lg bg-gray-50 p-2"><p className="text-[10px] font-semibold text-gray-500">{k}</p><p className="text-xs text-gray-800">{v || '—'}</p></div>
                  ))}
                </div>
              )}
              <p className="text-xs text-gray-800"><b className="text-gray-500 font-semibold">관측</b> <Rich t={o.observation} /></p>
              {o.interpretation && <p className="text-xs text-gray-800 mt-1"><b className="text-gray-500 font-semibold">해석</b> <Rich t={o.interpretation} /><Inference on={o.is_inference} /></p>}
              {o.counter_evidence && <p className="text-xs text-gray-500 mt-1"><b className="font-semibold">반증 여지</b> {o.counter_evidence}</p>}
              <Quotes list={o.evidence_quotes} />
            </div>
          ))}
        </div>
        {(ev.value_language ?? []).length > 0 && (<>
          <H tag="원문 분류">고객이 쓰는 자사의 가치 언어</H>
          <Quotes list={ev.value_language.map(v => v.phrase)} />
        </>)}
        {(nv.opportunity?.gaps ?? []).length > 0 && (<>
          <H>메워야 할 갭 — 기회가 아니라 부채</H>
          <Table head={['갭', '현황', '경쟁 기준선', '성격']} rows={nv.opportunity.gaps.map(g => [g.gap, g.status, g.baseline || '—', g.nature])} />
        </>)}
      </Section>

      {/* ── 7. 권고 ── */}
      <Section n="07" id="s7" title="권고 및 결정 요청" lead="무엇을 해야 하고, 무엇을 결정해 주셔야 하는가."
        keyMsg={nv.recommendation?.position?.statement}>
        <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-3.5 mb-3">
          <p className="text-xs font-bold text-indigo-700 mb-1">권고 포지션</p>
          <p className="text-sm text-gray-900 font-semibold">{nv.recommendation?.position?.statement}</p>
          <ul className="mt-1 text-xs text-gray-700 space-y-0.5">{(nv.recommendation?.position?.rationale ?? []).map((r, i) => <li key={i}>· {r}</li>)}</ul>
        </div>
        <H tag="저비용 우선">액션 — 우선순위순</H>
        <Table head={['#', '액션', '근거', '시점', '비용']}
          rows={(nv.recommendation?.actions ?? []).map(a => [a.id,
            <span><b>{a.action}</b>{a.detail && <span className="block text-[11px] text-gray-500 mt-0.5">{a.detail}</span>}</span>,
            <Rich t={a.rationale} />,
            <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold ${String(a.timing).startsWith('즉시') ? 'bg-red-50 text-red-700' : 'bg-gray-100 text-gray-600'}`}>{a.timing}</span>,
            <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold ${{ '없음': 'bg-gray-100', '낮음': 'bg-green-50', '중간': 'bg-amber-50', '높음': 'bg-red-50' }[a.cost] ?? ''} ${COST[a.cost] ?? ''}`}>{a.cost}</span>])} />
        {(nv.recommendation?.timing_notes ?? []).length > 0 && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-3 mb-2">
            <p className="text-[11px] font-semibold text-red-800 mb-1">⏱ 시점 — 놓치면 선택지가 줄어드는 날짜</p>
            <ul className="text-xs text-red-900 space-y-0.5">{nv.recommendation.timing_notes.map((t, i) => <li key={i}>· <Rich t={t} /></li>)}</ul>
          </div>
        )}
        {(nv.recommendation?.kpi_layers ?? []).length > 0 && (<>
          <H>KPI 후보 — 어느 층위로 이 상품을 평가할 것인가</H>
          <Table head={['층위', '지표', '현재 상태', '이 층위만 KPI일 때의 부작용']}
            rows={nv.recommendation.kpi_layers.map(k => [k.layer, k.metric, k.current_state, k.side_effect])} />
          <Take t={nv.recommendation.kpi_note} />
        </>)}
        <H>결정 요청</H>
        <ol className="space-y-1.5">
          {(nv.recommendation?.decision_requests ?? []).map((d, i) => (
            <li key={i} className="text-sm text-gray-800 flex gap-2 rounded-lg border border-amber-200 border-l-4 border-l-amber-500 bg-amber-50/40 p-2.5">
              <span className="font-mono text-amber-600 font-bold">{'①②③④⑤⑥⑦'[i]}</span>
              <span><b>{d.title}</b><span className="block text-xs text-gray-700 mt-0.5 leading-relaxed"><Rich t={d.body} /></span></span>
            </li>
          ))}
        </ol>
        <H>이 권고의 한계</H>
        <ul className="text-xs text-gray-600 space-y-1">{(nv.recommendation?.limitations ?? []).map((s, i) => <li key={i}>· {s}</li>)}</ul>
      </Section>

      {/* ── 부록 ── */}
      <Section n="A" id="sA" title="부록 · 근거 자료" lead="이 리포트가 답하지 못한 것과 생성 정보.">
        <H>데이터 갭 — 이 리포트가 답하지 못한 것</H>
        <ul className="text-xs text-gray-600 space-y-1">{(nv.data_gaps ?? []).map((s, i) => <li key={i}>· {s}</li>)}</ul>
        {(c.pending ?? []).length > 0 && (<>
          <H>축약판에서 제외된 블록</H>
          <ul className="text-xs text-gray-500 space-y-0.5">{c.pending.map((p, i) => <li key={i}>· {p.section} — {p.block} ({p.needs})</li>)}</ul>
        </>)}
        {X && (<>
          <H tag="LLM 탐색 · 구절은 서버가 받은 원문과 대조">외부 조사 출처 — {X.sources.length}건 (기준일 {X.as_of})</H>
          {[['history', '변경 이력'], ['finance', '공표 지표'], ['market', '시장']].map(([t, lab]) => (
            <div key={t} className="mb-2">
              <p className="text-[11px] font-semibold text-gray-500">{lab}</p>
              <ul className="text-[11px] text-gray-600 space-y-0.5">
                {X.sources.filter(d => d.track === t).map(d => (
                  <li key={d.doc_id}><span className="font-mono text-gray-400 mr-1">[{d.doc_id}]</span>
                    <a href={d.url} target="_blank" rel="noreferrer" className="hover:underline">{d.title}</a>
                    <span className="text-gray-400"> · {d.published_at}</span>
                    {d.source_check && <span className={`ml-1 text-[10px] ${d.source_check === '확인' ? 'text-green-600'
                      : d.source_check === '불일치' ? 'text-red-600' : 'text-gray-400'}`}>
                      원문 대조 {d.source_check}{d.excerpts_found ? ` ${d.excerpts_found}` : ''}</span>}</li>
                ))}
              </ul>
            </div>
          ))}
          {Object.keys(X.warnings ?? {}).length > 0 && (
            <ul className="text-xs text-amber-700 space-y-0.5">{Object.entries(X.warnings).flatMap(([t, ws]) => ws.map((w, i) => <li key={t + i}>⚑ {t}: {w}</li>))}</ul>
          )}
        </>)}
        {(report.warnings ?? []).length > 0 && (<>
          <H>작성 규칙 점검 경고</H>
          <ul className="text-xs text-amber-700 space-y-0.5">{report.warnings.map((w, i) => <li key={i}>⚑ {w}</li>)}</ul>
        </>)}
        <p className="mt-3 text-[10px] text-gray-400">
          생성 · {Object.entries(c.generation ?? {}).filter(([k]) => k !== 'inputs').map(([k, v]) => `${k}: ${v}`).join(' / ')}
          {X && ` / 외부 조사: ${Object.entries(X.generation ?? {}).map(([t, g]) => `${t}(${Object.values(g ?? {}).join(', ')})`).join(' · ')}`}
          {c.generation?.inputs && ` · 입력: 군집 ${c.generation.inputs.cluster?.slice(0, 10)} · 매트릭스 ${String(c.generation.inputs.matrix_snapshot ?? '').slice(0, 8)}`}
          {' · '}{report.rubric_version} · 점수 {report.evaluation_score}
        </p>
      </Section>
    </div>
  );
}
