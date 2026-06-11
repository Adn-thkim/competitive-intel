"""
collect_clien_community.py
--------------------------
clien(domain_class="community") URL 만 community_collection 의 실제 경로
(select → robots → fetch → 적재)로 흘려보내 `community_posts` 를 생성·검증하는
1회성 스크립트.

목적
----
앞선 진단에서 clien 은 (1) 차단 아님(HTTP 200·_fetch_content ok), (2) robots
허용(/service/board/), (3) 매핑에 존재·정상 선별 임이 확인됐고, 06-06 수집 실행의
누락은 일시적 fetch_failed 로 추정됐다. 본 스크립트로 지금 시점에 clien 이 실제로
community_posts 에 적재되는지 확인한다.

본문 수집 정책 (사용자 요청 반영)
---------------------------------
- **200자 SPA 임계 미적용**: `_fetch_content` 의 fetch_status 가 `ok` 든
  `requires_dynamic_render` 든, content 가 비어있지 않으면 본문으로 채택한다.
  (임계는 분류용일 뿐 content 자체는 항상 반환됨 — <200자도 버리지 않음.)
- **발췌 상한 미적용**: 노드의 `_BODY_EXCERPT_CHARS=2000` 절단 없이 본문 전체를
  적재한다. (단, `_fetch_content` 내부 안전 상한 `_FULLTEXT_CAP=50,000자` 는 유지 —
  clien 게시글은 이보다 훨씬 짧아 사실상 '본문 모두'.)

  ⚠️ 트레이드오프: 운영 노드에 이 정책을 반영하려면 `_fetch_content`/
  `community_collection_node` 패치가 필요하다. 임계 제거 시 JS 셸 페이지(실제 본문
  거의 없음)도 본문으로 적재되어 reaction 분석에 노이즈가 섞일 수 있다. 본 스크립트는
  로컬 검증용 override 일 뿐 운영 코드는 건드리지 않는다.

실행 (clien 접근 가능한 로컬 네트워크)
--------------------------------------
    cd competitive-intel
    python scripts/collect_clien_community.py

결과: scripts/out/clien_community_posts.json + 콘솔 요약.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import requests  # noqa: E402

from server.graph.nodes.community_collection_node import (  # noqa: E402
    select_community_urls,
    _robots_allowed,            # 노드 현행(버그) 동작 — 비교용
    _title_of,
    _RATE_LIMIT_SEC,
)
from server.graph.nodes.official_content_collection_node import (  # noqa: E402
    _fetch_content,
    _FETCH_USER_AGENT,
    _FETCH_TIMEOUT,
)


def _robots_allowed_fixed(url: str) -> bool:
    """robots.txt 를 _fetch_content 와 동일 UA(requests) 로 읽어 판정.

    노드 현행 `_robots_allowed` 의 false-negative 교정:
    urllib.robotparser.read() 는 기본 UA(Python-urllib)로 robots.txt 를 받아오는데,
    clien 은 이 UA 에 403 을 주고 robotparser 는 403 -> disallow_all=True 로 전면
    금지 처리한다. 실제 robots.txt 는 /service/board/ 를 Allow 하므로, 본문 fetch 와
    동일 UA 로 robots.txt 를 받아 정상 규칙을 적용한다.
    """
    parsed = urlparse(url)
    host = f"{parsed.scheme}://{parsed.netloc}"
    try:
        resp = requests.get(f"{host}/robots.txt", timeout=_FETCH_TIMEOUT,
                            headers={"User-Agent": _FETCH_USER_AGENT})
    except Exception:  # noqa: BLE001 — 조회 불가 -> 허용 (RFC 9309 관행, 노드와 동일)
        return True
    if resp.status_code in (401, 403):
        return False                  # 진짜 인증 차단
    if resp.status_code >= 400:
        return True                   # 기타 4xx/5xx -> robots 없음으로 간주 -> 허용
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp.can_fetch("*", url)

_MAPPING_CACHE = _ROOT / "data" / "cache" / "agent_outputs" / "feature_mapping_blog_community.json"
_OUT_PATH = _ROOT / "scripts" / "out" / "clien_community_posts.json"

# 본문 수집 정책 (사용자 요청) — 둘 다 비활성
_APPLY_SPA_THRESHOLD = False   # 200자 미만도 버리지 않음
_EXCERPT_CHARS = None          # 발췌 절단 없음 (본문 전체)


def _is_clien(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "clien.net" or host.endswith(".clien.net")


def _build_state_from_cache() -> dict:
    """매핑 캐시(4종 후보 엔트리)를 analysis_features 로 사용하는 최소 state 구성."""
    data = json.loads(_MAPPING_CACHE.read_text(encoding="utf-8"))
    # 4종 후보(트래블월렛 포함) 엔트리 선택
    entry = next(
        v for v in data["entries"].values()
        if "트래블월렛" in (v.get("input_summary") or "")
    )
    features = entry["output"]["features"]
    feature_ids = [f["feature_id"] for f in features]
    return {
        "selected_purposes": ["reaction_insight"],
        "selected_feature_ids": feature_ids,
        "analysis_features": features,
    }


def main() -> None:
    state = _build_state_from_cache()

    # 1) select — 노드의 실제 선별 로직
    selection = select_community_urls(state)

    # 2) clien(community) URL 만 추출
    clien_targets: list[dict] = []
    for cid in sorted(selection):
        for rec in selection[cid]:
            if _is_clien(rec["url"]):
                clien_targets.append({**rec, "candidate_id": cid})

    community_posts: list[dict] = []
    trace: list[dict] = []

    for rec in clien_targets:
        url = rec["url"]
        step = {"url": url, "candidate_id": rec["candidate_id"]}

        # 3) robots — 노드 현행(버그) vs 교정본 비교, 판정은 교정본 사용
        node_robots = _robots_allowed(url)            # urllib 기본 UA -> clien 403 -> False
        fixed_robots = _robots_allowed_fixed(url)     # _fetch_content 동일 UA -> 실제 규칙
        step["robots_node_buggy"] = node_robots
        step["robots_allowed"] = fixed_robots
        if not fixed_robots:
            step["result"] = "skipped_robots"
            trace.append(step)
            continue

        # 4) fetch (운영과 동일 _fetch_content)
        result = _fetch_content(url)
        if not result.get("from_cache"):
            time.sleep(_RATE_LIMIT_SEC)
        status = result.get("fetch_status", "fetch_failed")
        content = result.get("content", "") or ""
        step["fetch_status"] = status
        step["from_cache"] = result.get("from_cache", False)
        step["full_body_len"] = len(content)
        step["error"] = result.get("error", "")

        # 5) 적재 — 200자 임계 미적용: content 가 있으면 채택
        accept = bool(content.strip()) if not _APPLY_SPA_THRESHOLD else (status == "ok")
        if accept:
            body = content if _EXCERPT_CHARS is None else content[:_EXCERPT_CHARS]
            community_posts.append({
                "url": url,
                "candidate_id": rec["candidate_id"],
                "feature_ids": rec.get("feature_ids", []),
                "domain_class": rec.get("domain_class", "community"),
                "title": _title_of(content),
                "body_excerpt": body,          # 본문 전체 (절단 없음)
                "body_len": len(body),
                "published_at": rec.get("published_at", ""),
                "fetch_status": status,
            })
            step["result"] = "collected"
        else:
            step["result"] = "empty_body"
        trace.append(step)

    out = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "apply_spa_threshold_200": _APPLY_SPA_THRESHOLD,
            "excerpt_char_cap": _EXCERPT_CHARS,
            "note": "200자 임계·2000자 발췌 상한 미적용 — 본문 전체 적재",
        },
        "selected_clien_count": len(clien_targets),
        "community_posts": community_posts,
        "trace": trace,
    }
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 콘솔 요약
    print(f"\n결과 저장: {_OUT_PATH}")
    print(f"clien 선별 {len(clien_targets)}건 → community_posts {len(community_posts)}건 적재\n")
    print(f"{'candidate':26} {'status':20} {'robots(노드/교정)':16} {'본문길이':>8}  결과")
    print("-" * 88)
    for s in trace:
        robots = f"{s.get('robots_node_buggy','-')}/{s.get('robots_allowed','-')}"
        print(f"{s['candidate_id']:26} {str(s.get('fetch_status','-')):20} "
              f"{robots:16} {str(s.get('full_body_len','-')):>8}  "
              f"{s.get('result')}")
    if community_posts:
        p = community_posts[0]
        print(f"\n[예시] {p['candidate_id']} · 제목: {p['title'][:50]}")
        print(f"       본문 앞 120자: {p['body_excerpt'][:120].replace(chr(10),' ')}")
    print("\n검증 기준: community_posts 길이 ≥ 1, 각 본문 body_len > 0 이면 수집 성공.")


if __name__ == "__main__":
    main()
