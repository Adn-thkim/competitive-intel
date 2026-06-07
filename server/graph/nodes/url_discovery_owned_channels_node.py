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
# v0.13.6 — 5 → 20 상향 (Brave 과금은 요청 단위라 무비용. 트래블월렛 네이버 블로그
# 케이스: 공식 블로그 홈이 상위 5위 밖 — Google 대비 Brave 랭킹 격차 보완.
# count 는 Brave 캐시 키에 포함되므로 owned_channels 쿼리만 재검색됨)
_BRAVE_COUNT_PER_PLATFORM      = 20        # 각 platform 당 Brave 상위 20개 URL
_LLM_CONFIDENCE_THRESHOLD      = 0.7        # D16 권장안 — 0.7 이상만 verified 채택
_HTTP_TIMEOUT                  = (3, 7)
_YOUTUBE_CHANNELS_ENDPOINT     = "https://www.googleapis.com/youtube/v3/channels"

# v0.13.3 — platform 별 허용 host 화이트리스트 (도메인 가드)
# LLM 검증이 official 사이트 페이지를 SNS 채널로 오판·통과시키는 사례 차단
# (예: x platform 에 m.hanacard.co.kr, youtube_official 에 shinhancard.com).
# press_release 는 자사 사이트·언론 도메인이 다양하므로 제한 없음.
# blog_self_hosted (v0.13.4) 는 역방향 가드 — 타 platform 도메인이면 불통과
# (_platform_host_allowed 에서 별도 처리).
_PLATFORM_ALLOWED_HOSTS: dict[str, tuple[str, ...]] = {
    "instagram":        ("instagram.com",),
    "x":                ("x.com", "twitter.com"),
    "youtube_official": ("youtube.com",),
    "blog_naver":       ("blog.naver.com",),
    "blog_tistory":     ("tistory.com",),
}

# v0.10.21 — 쿼리 템플릿 (D14: X 는 핸들까지만)
# v0.13.4 — blog_self_hosted 추가 (자체 호스팅 블로그, 예: blog.hanabank.com)
_PLATFORM_QUERIES: dict[str, str] = {
    "instagram":        "{candidate_name} instagram 공식 계정",
    "x":                "{candidate_name} 공식 X 트위터",
    "blog_naver":       "{candidate_name} 공식 블로그 네이버",
    "blog_tistory":     "{candidate_name} 공식 블로그 티스토리",
    "blog_self_hosted": "{candidate_name} 공식 블로그",
    "press_release":    "{candidate_name} 보도자료",
    "youtube_official": "{candidate_name} 공식 유튜브 채널",
}

# v0.13.4 — 법인 브랜드 기반 site: 한정 보조 쿼리.
# 실사(2026-06-07) 결과: 상품명 쿼리의 Brave 상위 5건에 실 채널 URL 부재가 미탐지의
# 주원인 — ① 채널은 법인 브랜드(하나카드·신한카드) 단위 운영, ② Brave 일반 검색은
# x.com 을 노출하지 않음(캐시 281개 쿼리에서 x.com 0건). site: 연산자 + 브랜드명으로
# platform 도메인 내 검색을 강제한다. press_release 는 보조 쿼리 불필요.
_PLATFORM_BRAND_QUERIES: dict[str, str] = {
    "instagram":        "site:instagram.com {candidate_brand} 공식",
    "x":                "site:x.com {candidate_brand}",
    "blog_naver":       "site:blog.naver.com {candidate_brand} 공식",
    "blog_tistory":     "site:tistory.com {candidate_brand} 공식",
    "blog_self_hosted": "{candidate_brand} 공식 블로그",
    "youtube_official": "site:youtube.com {candidate_brand} 공식 채널",
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
        # v0.13.4 — 브랜드 site: 쿼리 병행 + blog_self_hosted 추가 (캐시 무효화)
        prompt_version="url_discovery_owned_channels:v0.13.4",
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
        # v0.13.4 — 법인 브랜드 site: 보조 쿼리 (상품명 쿼리와 다를 때만 병행)
        brand_query = ""
        brand_tpl = _PLATFORM_BRAND_QUERIES.get(platform)
        if brand_tpl and cbrand:
            bq = brand_tpl.format(candidate_brand=cbrand)
            if bq != query:
                brand_query = bq

        # 1) 캐시 조회 (cache_input = {candidate_id, platform, query, brand_query, count})
        # v1.0.1 — count 를 키에 추가: count 5→20 상향(v0.13.6)이 캐시 히트에 가려져
        # 실제 재검색이 일어나지 않았던 결함 수정 (2026-06-07 실사 — Brave 캐시
        # 319건 전부 count=5). count 변경 시 자동으로 재탐색된다.
        cache_input = {
            "candidate_id": cid, "platform": platform,
            "query": query, "brand_query": brand_query,
            "count": _BRAVE_COUNT_PER_PLATFORM,
        }
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
        #    상품명 쿼리 + 브랜드 site: 쿼리 결과 합집합 (URL 기준 dedupe, 순서 보존)
        brave_results = _brave_search(query, count=_BRAVE_COUNT_PER_PLATFORM)
        if brand_query:
            brave_results = brave_results + _brave_search(
                brand_query, count=_BRAVE_COUNT_PER_PLATFORM,
            )
        seen_urls: set[str] = set()
        candidate_urls = []
        for r in brave_results:
            u = (r.get("url") or "").strip()
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            candidate_urls.append({
                "url":     u,
                "title":   (r.get("title") or "").strip(),
                "snippet": (r.get("description") or "").strip(),
            })
        if not candidate_urls:
            # v0.13.5 — 빈 결과를 캐시하지 않는다 (2026-06-07 회귀 교훈).
            # Brave rate limit 폭주로 전 호출이 실패한 실행에서, 빈 결과가 7일 TTL 로
            # 박제되어 재실행에도 전부 "미발견"으로 고정되는 사고 발생. Brave 실패와
            # 정상 0건을 호출부에서 구분할 수 없으므로 둘 다 캐시 금지 — 정상 0건의
            # 재호출 비용은 Brave 24h 캐시·rate limiter 가 흡수한다.
            # RuntimeError 로 올려 호출부 except 가 errors 에 적재 (실패 가시화).
            raise RuntimeError("Brave 후보 0건 (검색 실패 또는 무결과) — 캐시 미저장")

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
            # v0.13.3 도메인 가드 — platform-host 불일치 URL 은 confidence 와 무관하게 드롭
            if not _platform_host_allowed(h.get("url", ""), platform):
                logger.info(
                    "url_discovery_owned_channels: platform-도메인 불일치 드롭 (%s, %s)",
                    platform, h.get("url", ""),
                )
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

    # ── v1.0.2 (MS-D14) — YouTube 핸들 프로브 폴백 ──────────────────────────
    # Brave 가 채널을 노출하지 않는 케이스(실측: @travelwallet — 두 쿼리 40건 전부
    # 무관)는 검색으로 풀 수 없다. 발견 완료된 타 platform 핸들·도메인 slug 에서
    # 영문 핸들 후보를 유도해 channels.list?forHandle= 로 실존+신원(채널명 토큰)을
    # 검증한다. 후보당 1 unit, candidate당 최대 4 후보.
    if YOUTUBE_API_KEY:
        for cid, cname, cbrand in targets:
            found = results_by_candidate.get(cid, [])
            if any(h.get("platform") == "youtube_official" for h in found):
                continue
            probed = _probe_youtube_handles(
                _youtube_handle_candidates(found), cname, cbrand)
            if probed:
                logger.info(
                    "url_discovery_owned_channels: 핸들 프로브 적중 (%s → @%s, 채널명 %s)",
                    cid, probed["handle"], probed.get("channel_title", ""),
                )
                results_by_candidate.setdefault(cid, []).append(probed)

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

def _platform_host_allowed(url: str, platform: str) -> bool:
    """v0.13.3 도메인 가드 — URL host 가 platform 허용 도메인(또는 서브도메인)인지 검증.

    - _PLATFORM_ALLOWED_HOSTS 등재 platform: 해당 도메인(서브도메인 포함)만 통과.
    - blog_self_hosted (v0.13.4): 역방향 가드 — 타 platform 도메인이면 불통과
      (예: blog.naver.com 은 blog_naver 로 분류되어야 하므로 self_hosted 에서 제외).
    - 그 외 (press_release): 항상 통과.
    """
    def _host_of(u: str) -> str:
        return u.split("//", 1)[-1].split("/", 1)[0].split("?")[0].lower()

    def _matches(host: str, domains: tuple[str, ...]) -> bool:
        return any(host == d or host.endswith("." + d) for d in domains)

    if platform == "blog_self_hosted":
        if not url:
            return False
        host = _host_of(url)
        return not any(
            _matches(host, domains) for domains in _PLATFORM_ALLOWED_HOSTS.values()
        )

    allowed = _PLATFORM_ALLOWED_HOSTS.get(platform)
    if allowed is None:
        return True
    if not url:
        return False
    return _matches(_host_of(url), allowed)


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
      구형 URL (v0.13.3 보정):
        "https://www.youtube.com/user/TOSSservice"           → "TOSSservice"
        "https://www.youtube.com/c/TossBank"                 → "TossBank"
        "https://www.youtube.com/channel/UCxxx"              → "" (핸들 부재 —
        _fetch_youtube_channel_meta 가 URL 의 channel_id 로 직접 조회)
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

    if platform in ("press_release", "blog_self_hosted"):
        # press release · 자체 호스팅 블로그는 host + path 조합이라 단일 handle 부재
        # (UI 는 handle 부재 시 URL 자체를 표시)
        return ""

    # 그 외 platform — path 첫 segment
    raw = path.rstrip("/").split("?")[0].split("#")[0]
    if raw.startswith("@"):
        raw = raw[1:]
        return raw.split("/")[0]
    if platform == "youtube_official":
        # v0.13.3 — 구형 URL 형태 보정: /user/X·/c/X 는 둘째 segment 가 채널명,
        # /channel/UC… 는 핸들 네임스페이스 부재
        segs = raw.split("/")
        if segs[0] in ("user", "c") and len(segs) >= 2:
            return segs[1]
        if segs[0] == "channel":
            return ""
    return raw.split("/")[0]


_PROBE_MAX_CANDIDATES = 4   # MS-D14 — candidate당 프로브 상한 (1 unit/후보)
_PROBE_SUFFIXES = (".official", "_official", "official")
_GENERIC_NAME_TOKENS = {"카드", "체크카드", "신용카드"}


def _youtube_handle_candidates(found_handles: list[dict]) -> list[str]:
    """MS-D14 — 발견된 타 platform 핸들·URL host 에서 YouTube 핸들 후보 유도 (순수 함수).

    유도 규칙 (우선순위순, 중복 제거, 상한 _PROBE_MAX_CANDIDATES):
    - instagram·blog 핸들: 원형 + official 접미사 제거형
      (예: "travelwallet.official" → "travelwallet.official", "travelwallet")
    - press_release·blog_self_hosted URL host 의 SLD:
      (예: "travel-wallet.com" → "travel-wallet", "travelwallet")
    """
    out: list[str] = []

    def _add(h: str) -> None:
        h = h.strip().lower()
        if h and len(h) >= 3 and h not in out:
            out.append(h)

    for rec in found_handles:
        h = (rec.get("handle") or "").lower()
        if h:
            _add(h)
            for suf in _PROBE_SUFFIXES:
                if h.endswith(suf):
                    _add(h[: -len(suf)].rstrip("._-"))
        url = rec.get("url") or ""
        if rec.get("platform") in ("press_release", "blog_self_hosted") and url:
            host = url.split("//", 1)[-1].split("/", 1)[0].lower()
            if host.startswith("www."):
                host = host[4:]
            sld = host.split(".")[0]
            _add(sld)
            _add(sld.replace("-", ""))
    return out[:_PROBE_MAX_CANDIDATES]


def _channel_title_matches(title: str, cname: str, cbrand: str) -> bool:
    """프로브 신원 검증 — 채널명에 브랜드 또는 상품명 토큰이 포함되는지 (순수 함수)."""
    norm = (title or "").lower().replace(" ", "")
    if not norm:
        return False
    if cbrand and cbrand.lower().replace(" ", "") in norm:
        return True
    return any(
        t.lower() in norm
        for t in (cname or "").split()
        if len(t) >= 2 and t.lower() not in _GENERIC_NAME_TOKENS
    )


def _probe_youtube_handles(
    candidates: list[str], cname: str, cbrand: str, fetch_meta=None,
) -> dict | None:
    """MS-D14 — 핸들 후보를 channels.list?forHandle= 로 실존+신원 검증 (1 unit/후보).

    핸들은 전역 고유이므로 실존 시 채널명이 정확히 반환된다. 채널명에 브랜드/상품
    토큰이 없으면 동명 무관 채널로 보고 기각 (오탐 가드). fetch_meta 는 테스트 주입용.
    """
    fetch = fetch_meta or _fetch_youtube_channel_meta
    for h in candidates:
        url = f"https://www.youtube.com/@{h}"
        meta = fetch(url, h)
        if not meta:
            continue
        if not _channel_title_matches(meta.get("title", ""), cname, cbrand):
            logger.debug("핸들 프로브 기각 — @%s 채널명 불일치: %s", h, meta.get("title", ""))
            continue
        return {
            "url":            url,
            "platform":       "youtube_official",
            "handle":         h,
            "channel_title":  meta.get("title", ""),
            "account_scope":  "parent_company",   # 보수 분류 (D14 패턴) — UI 배지용
            "is_verified":    bool(meta.get("verified")),
            "follower_count": meta.get("subscriber_count"),
            "channel_id":     meta.get("channel_id"),
            "last_post_at":   None,
            "confidence":     0.75,
            "origin":         "youtube_handle_probe",
            "matched_report_types": ["marketing_social"],
        }
    return None


def _fetch_youtube_channel_meta(url: str, handle: str) -> dict | None:
    """YouTube channels.list 호출로 channel_id + subscriber_count + verified 수집.

    1 unit/call. URL 형태별 조회 파라미터 (v0.13.3 — 구형 URL 보정):
    - /channel/UC…  → id (channel_id 직접 조회)
    - /user/X       → forUsername (구형 username — handle 과 별개 네임스페이스)
    - 그 외 (/@X 등) → forHandle (handle 비어있으면 skip)
    """
    if not YOUTUBE_API_KEY:
        return None
    rest = url.split("//", 1)[-1]
    path = rest.split("/", 1)[1] if "/" in rest else ""
    segs = [s for s in path.split("?")[0].split("#")[0].split("/") if s]
    if segs and segs[0] == "channel" and len(segs) >= 2:
        lookup = {"id": segs[1]}
    elif segs and segs[0] == "user" and len(segs) >= 2:
        lookup = {"forUsername": segs[1]}
    elif handle:
        lookup = {"forHandle": handle if handle.startswith("@") else f"@{handle}"}
    else:
        return None
    params = {
        "part": "snippet,statistics,status",
        **lookup,
        "key":  YOUTUBE_API_KEY,
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
        "title":            (it.get("snippet") or {}).get("title", ""),   # v1.0.2 프로브 신원 검증용
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
