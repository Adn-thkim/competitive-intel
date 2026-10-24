import { useMemo, useState } from 'react';
import { pspKo, qtKo, subKo } from './reactionLabels';

/**
 * ReactionAxisReviewPage — 반응 파이프라인 #5 interrupt(reaction_axis_review) 검토 화면.
 *
 * 배경: reaction_review_node 가 interrupt(payload) 로 그래프를 멈춘다(확인 전용, 수정 미구현).
 * /state 는 interrupt_value 를 돌려주지 않으므로, 폴링으로 받은 전체 `state`
 * (reaction_clustered·reaction_axis)에서 표를 직접 도출한다. interrupt_value(payload)가
 * 있으면 우선 사용한다.
 *
 * "확인" → POST /api/approve(동기 재개, resume 무시됨) → reaction_report(LLM 없는 결정론 집계)
 * → END. onApproved(최종 응답)로 결과 반영.
 *
 * embedded=true: ResultView 의 '고객 반응 인사이트' 탭 안에서 렌더될 때(전체 화면 래퍼 제거).
 */
export default function ReactionAxisReviewPage({ state, payload, threadId, onApproved, onReset, embedded = false }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError]           = useState(null);

  // ── 표 데이터 도출: payload 우선, 없으면 state 에서 계산 ──────────────────────
  const view = useMemo(() => {
    const clustered = state?.reaction_clustered || {};
    const axis      = state?.reaction_axis || {};
    const units     = clustered.units || [];
    const questions = clustered.questions || [];
    const summary   = clustered.summary || {};

    const superToSub =
      payload?.super_to_sub || axis.super_to_sub || summary.super_to_sub || {};

    // super_aspect 별 perspective / question_type 비율 (payload 없으면 units/questions 에서 집계)
    const ratio = (items, superKey, valKey) => {
      const by = {};
      for (const it of items) {
        const s = it[superKey] || '기타';
        const v = it[valKey] || 'other';
        (by[s] = by[s] || {});
        by[s][v] = (by[s][v] || 0) + 1;
      }
      const out = {};
      for (const [s, counts] of Object.entries(by)) {
        const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
        out[s] = Object.fromEntries(
          Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .map(([k, n]) => [k, { count: n, pct: Math.round((n / total) * 100) }]),
        );
      }
      return out;
    };

    const pspBySuper =
      payload?.perspective_by_super || ratio(units, 'super_aspect', 'perspective');
    const qtBySuper =
      payload?.question_type_by_super || ratio(questions, 'super_aspect', 'question_type');

    const totals = payload?.totals || {
      units: units.length,
      questions: questions.length,
      super_aspects: Object.keys(superToSub).length,
    };
    const axisSource = payload?.axis_source ?? clustered.axis_source ?? '';
    return { superToSub, pspBySuper, qtBySuper, totals, axisSource };
  }, [state, payload]);

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch('/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // 동기 재개(background 미지정) — report 는 LLM 없는 결정론 집계라 즉시 완료.
        // #5 는 확인 전용이라 노드는 resume 값을 무시하지만, **빈 dict({})는 LangGraph 가
        // "재개값 없음"으로 취급해 interrupt 를 재발동**하므로 반드시 비어있지 않은 값을 보낸다.
        body: JSON.stringify({ thread_id: threadId, resume: { confirmed: true } }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? `서버 오류 (HTTP ${res.status})`);
      onApproved(data);
    } catch (e) {
      setError(e.message || '재개 중 오류가 발생했습니다.');
      setSubmitting(false);
    }
  }

  const { superToSub, pspBySuper, qtBySuper, totals, axisSource } = view;
  // label: enum id → 한글 라벨 함수(perspective/question_type). 고정 enum 이라 정적 매핑(무 LLM).
  const ratioText = (obj, label) =>
    Object.entries(obj || {})
      .map(([k, v]) => `${label ? label(k) : k} ${v.pct}%`)
      .join(' · ') || '—';

  const th = { textAlign: 'left', padding: '6px 10px', borderBottom: '2px solid #ddd', fontSize: 13 };
  const td = { padding: '6px 10px', borderBottom: '1px solid #eee', fontSize: 13, verticalAlign: 'top' };

  const wrap = embedded
    ? { fontFamily: 'system-ui, sans-serif' }
    : { maxWidth: 920, margin: '32px auto', padding: '0 20px', fontFamily: 'system-ui, sans-serif' };
  return (
    <div style={wrap}>
      <h2 style={{ marginBottom: 4 }}>군집 축 검토 (#5)</h2>
      <p style={{ color: '#666', fontSize: 13, marginTop: 0 }}>
        확인 전용(축 수정 미구현). 아래 축·비율을 확인하고 <b>확인</b>을 누르면 리포트 생성으로 진행합니다.
      </p>
      <div style={{ fontSize: 13, color: '#333', margin: '12px 0' }}>
        <b>총계</b> · units {totals.units} · questions {totals.questions} · super_aspect {totals.super_aspects}
        {axisSource && <> · 축 출처 <code>{axisSource}</code></>}
      </div>

      <h3 style={{ marginBottom: 6 }}>상위 축 → 하위 군집(빈도)</h3>
      <table style={{ borderCollapse: 'collapse', width: '100%', marginBottom: 24 }}>
        <thead>
          <tr><th style={th}>super_aspect</th><th style={th}>sub_cluster (n)</th></tr>
        </thead>
        <tbody>
          {Object.entries(superToSub).map(([sa, subs]) => (
            <tr key={sa}>
              <td style={{ ...td, fontWeight: 600, whiteSpace: 'nowrap' }}>{sa}</td>
              <td style={td}>
                {Object.entries(subs)
                  .sort((a, b) => b[1] - a[1])
                  .map(([subLabel, n]) => `${subKo(subLabel)} (${n})`)
                  .join(', ')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 style={{ marginBottom: 6 }}>축별 관점(perspective) 비율</h3>
      <table style={{ borderCollapse: 'collapse', width: '100%', marginBottom: 24 }}>
        <thead>
          <tr><th style={th}>super_aspect</th><th style={th}>perspective 분포</th></tr>
        </thead>
        <tbody>
          {Object.entries(pspBySuper).map(([sa, obj]) => (
            <tr key={sa}>
              <td style={{ ...td, fontWeight: 600, whiteSpace: 'nowrap' }}>{sa}</td>
              <td style={td}>{ratioText(obj, pspKo)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 style={{ marginBottom: 6 }}>축별 질문 유형(question_type) 비율</h3>
      <table style={{ borderCollapse: 'collapse', width: '100%', marginBottom: 24 }}>
        <thead>
          <tr><th style={th}>super_aspect</th><th style={th}>question_type 분포</th></tr>
        </thead>
        <tbody>
          {Object.entries(qtBySuper).map(([sa, obj]) => (
            <tr key={sa}>
              <td style={{ ...td, fontWeight: 600, whiteSpace: 'nowrap' }}>{sa}</td>
              <td style={td}>{ratioText(obj, qtKo)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {error && <p style={{ color: '#c0392b', fontSize: 13 }}>⚠️ {error}</p>}

      <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
        <button
          onClick={handleConfirm}
          disabled={submitting}
          style={{ padding: '10px 20px', fontSize: 14, fontWeight: 600, cursor: submitting ? 'default' : 'pointer',
                   background: submitting ? '#9bb7e6' : '#2d6cdf', color: '#fff', border: 'none', borderRadius: 6 }}
        >
          {submitting ? '리포트 생성 중…' : '확인 (리포트 생성)'}
        </button>
        {onReset && (
          <button onClick={onReset} disabled={submitting}
            style={{ padding: '10px 20px', fontSize: 14, cursor: 'pointer', background: '#f2f2f2',
                     color: '#333', border: '1px solid #ccc', borderRadius: 6 }}>
            처음으로
          </button>
        )}
      </div>
    </div>
  );
}
