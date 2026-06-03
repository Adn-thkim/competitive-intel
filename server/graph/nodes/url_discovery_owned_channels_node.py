"""
server/graph/nodes/url_discovery_owned_channels_node.py (v0.10.21 실 구현)
-------------------------------------------------------------------------
5중 fan-out 중 source-type 4번 — marketing_social 자사·경쟁사 운영 채널 탐색.

v0.10.19 까지의 스켈레톤(빈 결과 반환) 을 폐기하고 본 PR(v0.10.21) 에서 실 구현 도입.

역할 (v0.10.21)
----------------
1. candidate 별 platform 5종 Brave 쿼리:
   - instagram      : "{candidate_name} instagram 공식 계정"
   - x              : "{candidate_name} 공식 X 트위터"
   - blog_naver     : "{candidate_name} 공식 블로그 네이버"
   - blog_tistory   : "{candidate_name} 공식 블로그 티스토리"
   - press_release  : "{candidate_name} 보도자료"
   - youtube_official: "{candidate_name} 공식 유튜브 채널"
2. Brave 결과 상위 5개 URL 을 후보로 수집
3. LLM 검증 (`ClaudeCodeCliAnalyzer` — turn-49 사용자 결정):
   - 입력: {candidate_name, candidate_brand, platform, candidate_urls}
   - 출력: verified_handles = [{url, is_official, account_scope, confidence, rationale}, ...]
   - confidence ≥ 0.7 채택 (D16 권장안)
   - 다중 공식 계정 모두 반환 + account_scope 분류 (D17 권장안)
   - 결정론성은 system_prompt 의 명확한 판정 기준(URL 의 official 접미사·snippet 의
     "공식" 키워드·도메인 일치 등) 으로 자연어 수준에서 흡수. ProductIdResolver 같은
     완전 결정론(slug 생성) 영역과 달리 confidence 미세 변동(예: 0.85↔0.87) 은
     임계 0.7 판정에 무관하므로 CLI 비결정성 영향 미미.
4. YouTube platform 한정 추가: `channels.list?forHandle=...` (1 unit) 호출로
   channel_id·subscriber_count·verified 확정. cross_reference_node(v0.10.26) 가
   youtube_reactions 영상 필터링 시 본 channel_id 활용.

D14·D16·D17 결정 항목 권장안 채택
----------------------------------
- D14 X 처리: 1차 핸들 발견까지만, 본문 metadata 미수집
- D16 confidence 임계: 0.7 미만 → is_official=false (system_prompt 정책)
- D17 다중 공식 계정: 모든 발견 핸들 반환 + account_scope 분류

캐싱
----
- agent_id = "url_discovery_owned_channels"
- cache_input = {candidate_id, platform, query}
- TTL = 7일 (공식 핸들은 자주 변경되지 않음 + LLM 검증 비용 절감)

graceful 종료
-------------
- Brave API key 미설정: status="skipped"
- 부분 실패 (일부 platform LLM 실패): status="completed" + errors 누적
- 전체 실패: 빈 결과 + status="completed" (다른 4개 노드 진행)

LLM 분석기 변경 (turn-49)
-------------------------
v0.10.21 초기는 `ClaudeApiAnalyzer(temperature=0)` 사용. 사용자 검토 결과 본 노드의
LLM 검증은 ProductIdResolver 같은 완전 결정론 영역이 아니므로 CLI 의 자연어 수준
결정론으로 충분 → `ClaudeCodeCliAnalyzer` 채택. ANTHROPIC_API_KEY graceful 분기 폐기
(CLI 는 Claude Pro/Max 구독 토큰 사용; ANTHROPIC_API_KEY 미사용).

위치 (v0.10.19 토폴로지 1차 fan-out)
------------------------------------
ab_join
  ├─→ url_discovery_official_node
  ├─→ url_discovery_blog_community_node
  ├─→ url_discovery_youtube_reactions_node
  ├─→ [url_discovery_owned_channels_node]   ← 이 노드 (v0.10.21 실 구현)
  └─→ url_discovery_macro_node
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

from server.config import (
    AGENTS_DIR,
    BRAVE_SEARCH_API_KEY,
    CLI_MODEL,
    CLI_TIMEOUT,
    YOUTUBE_API_KEY,
)
from server.graph.agent_cache import load_agent_output, make_cache_context, store_agent_output
from server.graph.progress_store import set_progress
from server.graph.state import DomainAnalysisState, AgentStep
from server.graph.nodes.feature_url_mapper_node import (
    _brave_search,
    _candidate_name_map,
    _error,
)
from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer

logger = logging.getLogger(__name__)


_OWNED_CHANNEL_CACHE_TTL_HOURS = 24 * 7   # 7일 (공식 핸들 변동 적음)
_BRAVE_COUNT_PER_PLATFORM      = 5         # 각 platform 당 Brave 상위 5개 URL
_LLM_CONFIDENCE_THRESHOLD      = 0.7        # D16 권장안 — 0.7 이상만 verified 채택
_HTTP_TIMEOUT                  = (3, 7)
_YOUTUBE_CHANNELS_ENDPOINT     = "https://www.googleapis.com/youtube/v3/channels"

# v0.10.21 — 5 platforms + 쿼리 템플릿 (D14: X 는 핸들까지만)
_PLATFORM_QUERIES: dict[str, str] = {
    "instagram":       "{candidate_name} instagram 공식 계정",
    "x":               "{candidate_name} 공식 X 트위터",
    "blog_naver":      "{candidate_name} 공식 블로그 네이버",
    "blog_tistory":    "{candidate_name} 공식 블로그 티스토리",
    "press_release":   "{candidate_name} 보도자료",
    "youtube_official": "{candidate_name} 공식 유튜브 채널",
}


def url_discovery_owned_channels_node(
    state: DomainAnalysisState, config: dict | None = None,
) -> dict:
    """v0.10.21 실 구현 — Brave 검색 + LLM 검증 + YouTube channels.list 으로 owned channels 발견.

    Returns
    -------
    dict
        {owned_channel_urls_by_candidate, agent_steps[+ errors]}
    """
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id  = (config or {}).get("configurable", {}).get("thread_id", "")

    print(
        f"📱 [url_discovery_owned_channels_node] ENTRY at {started_at} thread_id={thread_id!r}",
        flush=True,
    )

    if thread_id:
        try:
            set_progress(
                thread_id, "feature_mapping_brave",
                detail="Owned channels 탐색 (Brave + LLM 검증)",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress(owned_channels) 실패: %s", exc)

    # ── API key graceful 처리 ────────────────────────────────────────────────
    # CLI 어댑터(ClaudeCodeCliAnalyzer)는 Claude Pro/Max 구독 토큰 사용 → ANTHROPIC_API_KEY 무관
    if not BRAVE_SEARCH_API_KEY:
        return _skipped(started_at, "BRAVE_SEARCH_API_KEY 미설정")

    # ── 입력 수집 ───────────────────────────────────────────────────────────
    domain_name: str            = state.get("domain_name") or ""
    own_product: dict           = state.get("own_product") or {}
    competitor_candidates: list = state.get("competitor_candidates") or []
    selected_ids: list[str]     = state.get("selected_competitor_ids") or []

    if not domain_name:
        return _error(started_at, "domain_name 이 state 에 없습니다.")

    name_map = _candidate_name_map(own_product, competitor_candidates, selected_ids)

    # 처리 대상 candidate 의 (cid, name, brand) 튜플 목록
    targets: list[tuple[str, str, str]] = []
    own_id   = own_product.get("product_id") or "own"
    own_name = own_product.get("name") or own_product.get("product_name") or ""
    own_brand = own_product.get("brand") or own_name
    if own_name:
        targets.append((own_id, own_name, own_brand))
    for cand in competitor_candidates:
        cid = cand.get("candidate_id", "")
        if cid and (not selected_ids or cid in selected_ids):
            cname = cand.get("product_name") or cand.get("brand", "")
            cbrand = cand.get("brand", "") or cname
            if cname:
                targets.append((cid, cname, cbrand))

    if not targets:
        logger.info("url_discovery_owned_channels_node: 대상 candidate 0건")
        return _completed(started_at, {})

    # ── system_prompt + output_schema 로드 ───────────────────────────────────
    agent_dir = AGENTS_DIR / "url_discovery_owned_channels"
    sp_path   = agent_dir / "system_prompt_kr.md"
    sc_path   = agent_dir / "output.schema.json"
    if not sp_path.exists() or not sc_path.exists():
        return _error(
            started_at,
            f"agents/url_discovery_owned_channels/ 파일 부재: "
            f"system_prompt={sp_path.exists()}, schema={sc_path.exists()}",
        )
    system_prompt = sp_path.read_text(encoding="utf-8")
    output_schema = json.loads(sc_path.read_text(encoding="utf-8"))

    cache_context = make_cache_context(
        agent_id="url_discovery_owned_channels",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        # turn-49 CLI 어댑터 전환 — prompt_version 갱신으로 캐시 무효화
        prompt_version="url_discovery_owned_channels:v0.10.21.1",
    )

    # ── candidate × platform 6종 호출 (병렬) ─────────────────────────────────
    logger.info(
        "url_discovery_owned_channels_node: 시작 (candidates=%d × platforms=%d = %d 작업)",
        len(targets), len(_PLATFORM_QUERIES), len(targets) * len(_PLATFORM_QUERIES),
    )

    results_by_candidate: dict[str, list[dict]] = {}
    errors: list[dict[str, str]] = []

    # 작업 list (cid, cname, cbrand, platform)
    jobs = [
        (cid, cname, cbrand, platform)
        for cid, cname, cbrand in targets
        for platform in _PLATFORM_QUERIES
    ]

    analyzer = ClaudeCodeCliAnalyzer(
        model=CLI_MODEL, timeout=CLI_TIMEOUT, system_prompt=system_prompt,
    )

    def _process_one(cid: str, cname: str, cbrand: str, platform: str) -> tuple[str, list[dict]]:
        """(cand, platform) 단위 처리: Brave → LLM 검증 → (YT 한정) channels.list."""
        query = _PLATFORM_QUERIES[platform].format(candidate_name=cname)

        # 1) 캐시 조회 (cache_input = {candidate_id, platform, query})
        cache_input = {"candidate_id": cid, "platform": platform, "query": query}
        cached = load_agent_output(
            agent_id="url_discovery_owned_channels",
            cache_input=cache_input,
            context=cache_context,
            logger=logger,
            ttl_hours=_OWNED_CHANNEL_CACHE_TTL_HOURS,
        )
        if cached is not None:
            return cid, cached.get("handles", [])

        # 2) Brave 검색 (기존 _brave_search 헬퍼 재사용 — Brave 24h TTL 캐시 적용)
        brave_results = _brave_search(query, count=_BRAVE_COUNT_PER_PLATFORM)
        candidate_urls = [
            {
                "url":     (r.get("url") or "").strip(),
                "title":   (r.get("title") or "").strip(),
                "snippet": (r.get("description") or "").strip(),
            }
            for r in brave_results
            if r.get("url")
        ]
        if not candidate_urls:
            # 빈 결과 캐시 (반복 호출 방지)
            store_agent_output(
                agent_id="url_discovery_owned_channels",
                cache_input=cache_input, context=cache_context,
                output={"handles": []}, logger=logger,
            )
            return cid, []

        # 3) LLM 검증
        llm_input = {
            "candidate_name":  cname,
            "candidate_brand": cbrand,
            "platform":        platform,
            "domain_name":     domain_name,
            "candidate_urls":  candidate_urls,
        }
        prompt = (
            "다음 입력에 대해 system_prompt 의 판정 기준을 적용하여 verified_handles 를 산출하라.\n\n"
            f"{json.dumps(llm_input, ensure_ascii=False, indent=2)}"
        )
        try:
            llm_output = analyzer.call_with_schema(prompt=prompt, output_schema=output_schema)
        except RuntimeError as exc:
            raise RuntimeError(f"LLM 검증 실패: {exc}") from exc

        verified = llm_output.get("verified_handles", []) or []

        # 4) confidence ≥ 임계 + YouTube 한정 channels.list 보강
        final_handles: list[dict] = []
        for h in verified:
            conf = float(h.get("confidence", 0) or 0)
            if conf < _LLM_CONFIDENCE_THRESHOLD:
                continue
            handle: dict = {
                "url":           h.get("url", ""),
                "platform":      platform,
                "handle":        _extract_handle_from_url(h.get("url", ""), platform),
                "account_scope": h.get("account_scope", "parent_company"),
                "is_verified":   False,                  # bio fetch 시 갱신 가능
                "follower_count": None,                  # platform 별 보완 (YT 한정)
                "channel_id":    None,                   # YouTube 한정
                "last_post_at":  None,
                "confidence":    conf,
                "origin":        "owned_channel_search",
                "matched_report_types": ["marketing_social"],
            }
            # YouTube 공식 채널 한정: channels.list 로 channel_id + subscriber + verified
            if platform == "youtube_official" and YOUTUBE_API_KEY:
                yt_meta = _fetch_youtube_channel_meta(handle["url"], handle.get("handle") or "")
                if yt_meta:
                    handle["channel_id"]     = yt_meta.get("channel_id")
                    handle["follower_count"] = yt_meta.get("subscriber_count")
                    handle["is_verified"]    = bool(yt_meta.get("verified"))
            final_handles.append(handle)

        # 5) 캐시 저장
        store_agent_output(
            agent_id="url_discovery_owned_channels",
            cache_input=cache_input, context=cache_context,
            output={"handles": final_handles}, logger=logger,
        )
        return cid, final_handles

    with ThreadPoolExecutor(max_workers=3) as pool:
        future_map = {
            pool.submit(_process_one, *job): job for job in jobs
        }
        for fut in as_completed(future_map):
            cid, _cname, _cbrand, platform = future_map[fut]
            try:
                _c, handles = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "url_discovery_owned_channels: (%s, %s) 실패 — %s",
                    cid, platform, exc,
                )
                errors.append({
                    "node":      "url_discovery_owned_channels_node",
                    "error":     f"({cid}, {platform}): {str(exc)[:120]}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                continue
            for h in handles:
                results_by_candidate.setdefault(cid, []).append(h)

    total = sum(len(v) for v in results_by_candidate.values())
    logger.info(
        "url_discovery_owned_channels_node: 완료 (candidates=%d, 핸들 %d개, errors=%d)",
        len(results_by_candidate), total, len(errors),
    )

    finished_at = datetime.now(timezone.utc).isoformat()
    step: AgentStep = {
        "step_name":   "UrlDiscoveryOwnedChannels",
        "status":      "completed" if not errors else "completed",   # 부분 실패도 graceful
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패"

    out: dict = {
        "owned_channel_urls_by_candidate": results_by_candidate,
        "agent_steps":                     [step],
    }
    if errors:
        out["errors"] = errors
    return out


# ─────────────────────────────────── 헬퍼 ────────────────────────────────────

def _extract_handle_from_url(url: str, platform: str) -> str:
    """URL 에서 platform 별 handle 문자열 추출.

    platform 별 handle 위치:
    - instagram · x · blog_naver: path 첫 segment
      예: "https://www.instagram.com/travelwallet.official/" → "travelwallet.official"
          "https://x.com/tossteam"                            → "tossteam"
          "https://blog.naver.com/tossbank/123"               → "tossbank"
    - blog_tistory: host 의 첫 segment (subdomain)
      예: "https://hanamoney.tistory.com/"                   → "hanamoney"
    - youtube_official: path 첫 segment 의 @ prefix 제거
      예: "https://www.youtube.com/@TossBank"                → "TossBank"
    - press_release: 본 노드는 URL 자체를 핸들로 사용하지 않음 (빈 문자열 반환)
    """
    if not url:
        return ""
    # 스킴 제거
    rest = url.split("//", 1)[-1]
    host_path = rest.split("/", 1)
    host = host_path[0]
    path = host_path[1] if len(host_path) > 1 else ""

    if platform == "blog_tistory":
        # subdomain 첫 segment 추출 ("hanamoney.tistory.com" → "hanamoney")
        # www. 접두사 처리
        h = host.lower()
        if h.startswith("www."):
            h = h[4:]
        parts = h.split(".")
        # tistory.com 직전 segment
        return parts[0] if len(parts) >= 2 else h

    if platform == "press_release":
        # press release 는 host + path 조합이라 단일 handle 부재
        return ""

    # 그 외 platform — path 첫 segment
    raw = path.rstrip("/").split("?")[0].split("#")[0]
    if raw.startswith("@"):
        return raw[1:]
    return raw.split("/")[0]


def _fetch_youtube_channel_meta(url: str, handle: str) -> dict | None:
    """YouTube channels.list 호출로 channel_id + subscriber_count + verified 수집.

    1 unit/call. handle 이 비어있으면 호출 skip.
    """
    if not handle:
        return None
    if not YOUTUBE_API_KEY:
        return None
    params = {
        "part":      "snippet,statistics,status",
        "forHandle": handle if handle.startswith("@") else f"@{handle}",
        "key":       YOUTUBE_API_KEY,
    }
    try:
        resp = requests.get(_YOUTUBE_CHANNELS_ENDPOINT, params=params, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        logger.debug("youtube channels.list 네트워크 오류 (%s): %s", handle, exc)
        return None
    if not resp.ok:
        logger.debug(
            "youtube channels.list 응답 오류 %d (%s): %s",
            resp.status_code, handle, resp.text[:150],
        )
        return None
    items = resp.json().get("items") or []
    if not items:
        return None
    it = items[0]
    stats = it.get("statistics") or {}
    return {
        "channel_id":       it.get("id", ""),
        "subscriber_count": int(stats.get("subscriberCount", 0) or 0),
        "verified":         (it.get("status") or {}).get("isLinked", False),
    }


def _skipped(started_at: str, reason: str) -> dict:
    """API key 미설정 등 graceful skip."""
    logger.warning("url_discovery_owned_channels_node: skipped — %s", reason)
    finished_at = datetime.now(timezone.utc).isoformat()
    return {
        "owned_channel_urls_by_candidate": {},
        "agent_steps": [{
            "step_name":     "UrlDiscoveryOwnedChannels",
            "status":        "skipped",
            "started_at":    started_at,
            "finished_at":   finished_at,
            "error_message": reason,
        }],
    }


def _completed(started_at: str, result: dict) -> dict:
    """정상 완료 (빈 결과 포함)."""
    finished_at = datetime.now(timezone.utc).isoformat()
    return {
        "owned_channel_urls_by_candidate": result,
        "agent_steps": [{
            "step_name":   "UrlDiscoveryOwnedChannels",
            "status":      "completed",
            "started_at":  started_at,
            "finished_at": finished_at,
        }],
    }
