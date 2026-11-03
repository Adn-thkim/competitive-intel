/**
 * ReactionSourcesCard.jsx (2026-11 — 구 '수집 현황' + '공식 YouTube 채널' 카드 통합)
 * ----------------------------------------------------------------------------
 * 고객 반응 인사이트의 수집 대상 요약. feature_selection 시점은 영상·게시글 **탐색 완료**,
 * 댓글·본문 수집은 선택 완료 후 진행된다.
 *
 * Props
 *   - preview     : feature_selection_node._build_reaction_preview
 *                   {youtube: {total, by_candidate}, community: {total, by_candidate, by_site: [{site, total, by_candidate}]}}
 *                   (구 payload {youtube_count, community_count} 도 전체 수만 표시)
 *   - youtubeCard : feature_selection_node._build_owned_youtube_card — 열 순서·이름 + 제외 채널
 *                   {candidates: [{candidate_id, candidate_label, candidate_type, channels: [{url, handle, channel_id, channel_title}]}]}
 */
import { useState } from 'react';

// @핸들 (부재 시 채널 이름 → 채널 ID)
function channelLabel(ch) {
  if (ch.handle) return `@${ch.handle}`;
  if (ch.channel_title) return ch.channel_title;
  return ch.channel_id || ch.url;
}

const fmt = (n) => (typeof n === 'number' ? n.toLocaleString() : '–');

export default function ReactionSourcesCard({ preview, youtubeCard }) {
  const [sitesOpen, setSitesOpen] = useState(false);
  const cands = youtubeCard?.candidates ?? [];
  const yt = preview?.youtube ?? { total: preview?.youtube_count };
  const cm = preview?.community ?? { total: preview?.community_count };
  const sites = cm.by_site ?? [];
  const hasBreakdown = Boolean(yt.by_candidate || cm.by_candidate);
  const cell = (map, cid) => (map ? fmt(map[cid] ?? 0) : '–');
  const th = 'px-1 py-1.5 font-medium leading-tight text-center';

  return (
    <div className="rounded-lg border border-gray-200 bg-white mb-3">
      <div className="px-4 pt-3 pb-2 flex flex-wrap items-baseline justify-between gap-x-3">
        <h4 className="text-sm font-semibold text-gray-800">수집 대상</h4>
        <span className="text-[11px] text-gray-400">탐색 완료 · 댓글·본문 수집은 선택 후 진행</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[660px] table-fixed text-xs">
          <colgroup>
            <col />
            <col className="w-20" />
            {cands.map(c => <col key={c.candidate_id} className="w-[88px]" />)}
            <col className="w-7" />
          </colgroup>
          <thead className="text-[11px] text-gray-500 border-y border-gray-100 bg-gray-50">
            <tr>
              <th className="text-left pl-4 py-1.5 font-medium">채널</th>
              <th className={th}>전체(고유)</th>
              {cands.map(c => (
                <th
                  key={c.candidate_id}
                  title={c.candidate_label}
                  style={{ overflowWrap: 'anywhere' }}
                  className={`${th} break-keep ${c.candidate_type === 'own' ? 'text-indigo-600' : ''}`}
                >
                  {c.candidate_label}
                </th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody className="text-gray-700">
            <tr className="border-b border-gray-100">
              <td className="pl-4 py-2 whitespace-nowrap">
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-700 mr-1.5">YouTube</span>영상
              </td>
              <td className="text-center font-semibold">{fmt(yt.total)}</td>
              {cands.map(c => <td key={c.candidate_id} className="text-center">{cell(yt.by_candidate, c.candidate_id)}</td>)}
              <td />
            </tr>
            <tr
              className={`border-b border-gray-100 ${sites.length ? 'cursor-pointer hover:bg-gray-50' : ''}`}
              onClick={() => sites.length && setSitesOpen(v => !v)}
            >
              <td className="pl-4 py-2 whitespace-nowrap">
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-teal-50 text-teal-700 mr-1.5">커뮤니티</span>게시글
                {sites.length > 0 && <span className="ml-1 text-[10px] text-gray-400">사이트별</span>}
              </td>
              <td className="text-center font-semibold">{fmt(cm.total)}</td>
              {cands.map(c => <td key={c.candidate_id} className="text-center">{cell(cm.by_candidate, c.candidate_id)}</td>)}
              <td className="text-center text-[10px] text-gray-400">{sites.length ? (sitesOpen ? '▲' : '▼') : ''}</td>
            </tr>
            {sitesOpen && sites.map(s => (
              <tr key={s.site} className="text-gray-500 bg-gray-50/60">
                <td className="pl-9 py-1 text-[11px] truncate" title={s.site}>{s.site || '(미상)'}</td>
                <td className="text-center text-[11px]">{fmt(s.total)}</td>
                {cands.map(c => <td key={c.candidate_id} className="text-center text-[11px]">{fmt(s.by_candidate?.[c.candidate_id] ?? 0)}</td>)}
                <td />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hasBreakdown && (
        <p className="px-4 py-1.5 text-[10px] text-gray-400">
          영상·게시글 하나가 여러 상품에 해당할 수 있어 상품별 합계는 전체(고유)보다 클 수 있습니다.
        </p>
      )}

      {/* 제외 — 공식 YouTube 채널 (cross_reference 가 channel_id 로 영상 제외) */}
      <div className="border-t border-gray-100 bg-gray-50 rounded-b-lg px-4 py-2.5">
        <p className="text-[11px] text-gray-600 mb-1.5">
          <b className="font-semibold">제외</b> · 각 사가 직접 운영하는 공식 YouTube 채널의 영상은 광고·홍보성이므로 위 영상 수에서 제외했습니다.
        </p>
        {cands.length === 0 ? (
          <p className="text-[11px] text-gray-400 italic">공식 채널 식별 결과 없음.</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            {cands.map(c => (
              <div key={c.candidate_id} className="flex gap-1.5 min-w-0">
                <span className={`shrink-0 font-medium ${c.candidate_type === 'own' ? 'text-indigo-600' : 'text-gray-700'}`}>
                  {c.candidate_label}
                </span>
                {c.channels.length === 0 ? (
                  <span className="text-gray-400">공식 채널 없음</span>
                ) : (
                  <span className="min-w-0 truncate">
                    {c.channels.map((ch, i) => (
                      <span key={`${ch.url}-${i}`}>
                        {i > 0 && <span className="text-gray-300"> · </span>}
                        {!ch.channel_id && (
                          <span title="channel_id 미확정 — 이 채널 영상은 제외되지 않습니다" className="text-amber-500">⚠️</span>
                        )}
                        <a
                          href={ch.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={ch.url}
                          className="text-gray-600 hover:text-indigo-600 hover:underline"
                        >
                          {channelLabel(ch)}
                        </a>
                      </span>
                    ))}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
