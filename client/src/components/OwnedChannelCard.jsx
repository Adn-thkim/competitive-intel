/**
 * OwnedChannelCard.jsx (v0.10.28b D45 a — turn-64 신설)
 * ----------------------------------------------------------------
 * marketing_social 카드의 candidate × platform 매트릭스 컴포넌트.
 *
 * 책임 분리:
 *   - url_discovery_owned_channels_node (v0.10.21) — 공식 채널 URL 식별 완료
 *   - feature_mapping_owned_channels_node (v0.10.28b) — LLM 호출 생략, URL carry-through
 *   - feature_selection_node._build_owned_channels_card (v0.10.28b) — payload 산출
 *   - 본 컴포넌트 — candidate (자사·경쟁사) × platform (6종) 매트릭스 렌더링
 *   - v1.0 §6-6a 수집 노드 — 실제 SNS 게시물 빈도·콘텐츠 키워드 등 feature 값 산출
 *
 * Props
 *   - card : {candidates: [{candidate_id, candidate_label, candidate_type,
 *                          platforms: [{platform, platform_label, found, url,
 *                                       handle, account_scope, channel_id,
 *                                       subscriber_count, confidence}]}]}
 */

const ACCOUNT_SCOPE_LABEL = {
  parent_company:   '모회사',
  sub_brand:        '서브 브랜드',
  product_specific: '본 상품',
  regional:         '지역 한정',
};

function formatSubscribers(n) {
  if (n == null) return null;
  if (n >= 10000) return `${(n / 10000).toFixed(1)}만`;
  if (n >= 1000)  return `${(n / 1000).toFixed(1)}천`;
  return String(n);
}

function PlatformRow({ p }) {
  if (!p.found) {
    return (
      <li className="flex items-center gap-2 text-xs text-gray-400 py-0.5">
        <span className="text-gray-300 shrink-0">❌</span>
        <span className="font-medium text-gray-500 shrink-0 w-32">{p.platform_label}</span>
        <span className="text-gray-400 italic">미발견</span>
      </li>
    );
  }
  const scopeLabel = ACCOUNT_SCOPE_LABEL[p.account_scope] || p.account_scope;
  const subs = formatSubscribers(p.subscriber_count);
  return (
    <li className="flex items-center gap-2 text-xs text-gray-700 py-0.5">
      <span className="text-green-600 shrink-0">✅</span>
      <span className="font-medium text-gray-700 shrink-0 w-32">{p.platform_label}</span>
      <a
        href={p.url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-indigo-600 hover:underline truncate flex-1 min-w-0"
        onClick={(e) => e.stopPropagation()}
      >
        {p.handle ? `@${p.handle}` : p.url}
      </a>
      {scopeLabel ? (
        <span className="text-[10px] text-pink-700 bg-pink-50 px-1 rounded shrink-0">
          {scopeLabel}
        </span>
      ) : null}
      {subs ? (
        <span className="text-[10px] text-gray-600 bg-gray-100 px-1 rounded shrink-0">
          구독 {subs}
        </span>
      ) : null}
    </li>
  );
}

export default function OwnedChannelCard({ card }) {
  if (!card || !card.candidates || card.candidates.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4 my-3">
        <h4 className="text-sm font-semibold text-gray-700 mb-1">공식 채널</h4>
        <p className="text-xs text-gray-500 italic">
          candidate 별 공식 채널 식별 결과 없음.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-pink-200 bg-pink-50/30 p-4 my-3">
      <h4 className="text-sm font-semibold text-gray-700 mb-1">공식 채널</h4>
      <p className="text-xs text-gray-500 mb-3">
        candidate 별 공식 SNS·블로그·보도자료·YouTube 채널 식별 결과 — v1.0 §6-6a 수집 노드의 입력
      </p>
      <div className="space-y-3">
        {card.candidates.map((cand) => (
          <div key={cand.candidate_id} className="bg-white border border-gray-200 rounded p-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold text-gray-800">
                {cand.candidate_label}
              </span>
              <span
                className={
                  cand.candidate_type === 'own'
                    ? 'text-[10px] text-indigo-700 bg-indigo-50 px-1.5 py-0.5 rounded'
                    : 'text-[10px] text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded'
                }
              >
                {cand.candidate_type === 'own' ? '자사' : '경쟁사'}
              </span>
            </div>
            <ul className="space-y-0">
              {cand.platforms.map((p) => (
                <PlatformRow key={p.platform} p={p} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
