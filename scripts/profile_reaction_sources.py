"""
scripts/profile_reaction_sources.py
------------------------------------
reaction_insight 시리즈 실측 도구 (로컬 실행 전용 — 실제 네트워크·YouTube API 호출).

서브커맨드
----------
1) community  — RI-D8: blog_community URL 의 정적 fetch 통과율 실측
     python3 scripts/profile_reaction_sources.py community
   → community_collection_node 를 실데이터로 실행. host·domain_class 별
     ok / requires_dynamic_render / fetch_failed / robots 집계 출력.
     결과: RI-D7 채널 가중치·루브릭 4점 요건(2채널) 판단 근거.

2) youtube    — youtube_reaction_collection 실측 (quota ~30 units, 과금 없음)
     python3 scripts/profile_reaction_sources.py youtube
   → 영상 선별·댓글 수집 실행 후 후보/선별 영상 수, 댓글 필터 통과율,
     좋아요 분포, 샘플 댓글을 출력. 재실행은 24h 캐시 적중(quota 0).

3) absa       — reaction_analysis 실측 (실제 CLI 호출 candidate당 1회 = 4회, 수 분)
     python3 scripts/profile_reaction_sources.py absa
   → 수집 2노드(캐시 적중) 실행으로 원자료 확보 후 reaction_analysis 를 실 CLI 로
     실행. candidate별 tuple 수·aspect/polarity 분포·가드 제거·suggestion·샘플 출력.
     재실행은 agent 캐시 적중(CLI 0회).

산출물: data/collection/reaction_profile/{community|youtube|absa}/*.json
"""

import json
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "collection" / "reaction_profile"


# ─── 공용: reaction 계열 analysis_features 재구성 (캐시 기반) ────────────────

def _load_latest_features(agent_file: str) -> list[dict]:
    path = ROOT / "data" / "cache" / "agent_outputs" / agent_file
    data = json.loads(path.read_text())
    _h, entry = max(data["entries"].items(),
                    key=lambda kv: kv[1].get("created_at", ""))
    print(f"캐시 항목: {agent_file} (created {entry.get('created_at', '')[:19]})")
    return entry["output"]["features"]


def build_reaction_state(source_file: str) -> dict:
    features = [f for f in _load_latest_features(source_file)
                if f.get("report_type") == "reaction_insight"]
    fids = sorted({f["feature_id"] for f in features})
    cids = sorted({c.get("candidate_id", "")
                   for f in features for c in f.get("candidate_coverage") or []})
    print(f"reaction_insight features {len(fids)}종 × candidates {cids}")
    return {
        "selected_purposes":    ["reaction_insight"],
        "selected_feature_ids": fids,            # 전체 선택 시뮬레이션
        "analysis_features":    features,
    }


# ─── 1) community — RI-D8 통과율 ─────────────────────────────────────────────

def run_community() -> int:
    from server.graph.nodes.community_collection_node import (
        community_collection_node, select_community_urls)

    state = build_reaction_state("feature_mapping_blog_community.json")
    selection = select_community_urls(state)
    total_urls = sum(len(v) for v in selection.values())
    print(f"\n선별 URL: {total_urls}건 (candidate {len(selection)})")

    start = time.perf_counter()
    out = community_collection_node(state)
    elapsed = time.perf_counter() - start

    posts = out.get("community_posts", [])
    errors = out.get("errors", [])
    by_status = Counter()
    by_host_status: dict[str, Counter] = {}
    for p in posts:
        by_status["ok"] += 1
        host = urlparse(p["url"]).hostname or "?"
        by_host_status.setdefault(host, Counter())["ok"] += 1
    for e in errors:
        status, _, url = e["error"].partition(": ")
        by_status[status] += 1
        host = urlparse(url).hostname or "?"
        by_host_status.setdefault(host, Counter())[status] += 1

    print(f"\n[RI-D8 통과율] 총 {total_urls}건 → {dict(by_status)} | {elapsed:.1f}s")
    rate = by_status["ok"] / total_urls * 100 if total_urls else 0
    print(f"  정적 fetch 통과율: {rate:.0f}%")
    print("\n[host별]")
    for host in sorted(by_host_status):
        print(f"  {host:36s} {dict(by_host_status[host])}")
    print("\n[수집 게시글 미리보기]")
    for p in posts[:5]:
        print(f"  · ({p['domain_class'] or '-'}) {p['title'][:40]} — "
              f"{len(p['body_excerpt'])}자  {p['url'][:60]}")

    out_dir = OUT_DIR / "community"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "community_posts.json").write_text(
        json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ 저장: {(out_dir / 'community_posts.json').relative_to(ROOT)}")
    print("판단 가이드: 통과율 ≥ 50% 면 2채널 운영 가능(루브릭 4점), "
          "< 30% 면 RI-D7 가중치 하향 + Playwright(v0.11) 우선순위 상향 검토.")
    return 0


# ─── 2) youtube — 수집 실측 ──────────────────────────────────────────────────

def run_youtube() -> int:
    import server.graph.nodes.youtube_reaction_collection_node as yrc
    from server.llm.youtube_client import (
        current_quota_used, youtube_comment_threads)

    state = build_reaction_state("feature_mapping_youtube_reactions.json")
    selection = yrc.select_videos(state)
    n_videos = sum(len(v) for v in selection.values())
    print(f"\n선별 영상: {n_videos}건 (candidate {len(selection)})")
    for cid, videos in sorted(selection.items()):
        for v in videos:
            print(f"  {cid[:24]:24s} {v['video_id']} 조회수 {v['view_count']:>9,} "
                  f"features={[f.removeprefix('feat_') for f in v['feature_ids']]}")

    start = time.perf_counter()
    out = yrc.youtube_reaction_collection_node(state)
    elapsed = time.perf_counter() - start

    comments = out.get("selected_comments", [])
    videos = out.get("collected_videos", [])
    print(f"\n[수집 결과] 영상 {len(videos)} · 채택 댓글 {len(comments)} · "
          f"{elapsed:.1f}s · quota 사용 {current_quota_used()} units")
    for e in out.get("errors", []):
        print(f"  ✗ {e['error'][:90]}")

    # 필터 통과율 (캐시 적중 재호출 — quota 0)
    print("\n[영상별 필터 통과율 · 좋아요 분포]")
    for v in videos:
        try:
            raw = youtube_comment_threads(v["video_id"])
        except Exception:  # noqa: BLE001
            continue
        kept = [c for c in comments if c["video_id"] == v["video_id"]]
        likes = sorted((c["like_count"] for c in kept), reverse=True)
        top = likes[0] if likes else 0
        med = likes[len(likes) // 2] if likes else 0
        print(f"  {v['video_id']}  원시 {len(raw):3d} → 채택 {len(kept):2d}"
              f"  좋아요 max {top:,} / median {med}  «{v['title'][:30]}»")

    print("\n[샘플 댓글 5건]")
    for c in sorted(comments, key=lambda c: -c["like_count"])[:5]:
        print(f"  ♥{c['like_count']:<5} {c['text'][:70]}")

    out_dir = OUT_DIR / "youtube"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "collected_videos.json").write_text(
        json.dumps(videos, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "selected_comments.json").write_text(
        json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ 저장: {(out_dir).relative_to(ROOT)}/")
    return 0


# ─── 3) absa — reaction_analysis 실측 (실 CLI 호출) ──────────────────────────

def run_absa() -> int:
    from server.graph.nodes.youtube_reaction_collection_node import (
        youtube_reaction_collection_node)
    from server.graph.nodes.community_collection_node import community_collection_node
    from server.graph.nodes.reaction_analysis_node import reaction_analysis_node

    # 채널 2종 analysis_features 병합 + 트래블카드 taxonomy (aspect_codebook)
    yt_feats = [f for f in _load_latest_features("feature_mapping_youtube_reactions.json")
                if f.get("report_type") == "reaction_insight"]
    cm_feats = [f for f in _load_latest_features("feature_mapping_blog_community.json")
                if f.get("report_type") == "reaction_insight"]
    taxonomy = json.loads((ROOT / "data" / "taxonomy" / "3_slug.json").read_text())
    fids = sorted({f["feature_id"] for f in yt_feats + cm_feats})
    state = {
        "selected_purposes":    ["reaction_insight"],
        "selected_feature_ids": fids,
        "analysis_features":    yt_feats + cm_feats,
        "domain_taxonomy":      taxonomy,
    }

    print("\n[1/3] YouTube 댓글 수집 (캐시 적중 기대)…")
    yt_out = youtube_reaction_collection_node(state)
    state.update({k: v for k, v in yt_out.items()
                  if k in ("collected_videos", "selected_comments")})
    print(f"  영상 {len(state.get('collected_videos', []))} · "
          f"댓글 {len(state.get('selected_comments', []))}")

    print("[2/3] 커뮤니티 게시글 수집 (캐시 적중 기대)…")
    cm_out = community_collection_node(state)
    state["community_posts"] = cm_out.get("community_posts", [])
    print(f"  게시글 {len(state['community_posts'])}")

    print("[3/3] reaction_analysis 실행 — CLI candidate당 1회 (수 분 소요)…")
    start = time.perf_counter()
    out = reaction_analysis_node(state)
    elapsed = time.perf_counter() - start

    analysis = out.get("reaction_analysis", {})
    out_dir = OUT_DIR / "absa"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[ABSA 결과] {len(analysis)} candidates · {elapsed:.0f}s")
    for e in out.get("errors", []):
        print(f"  ✗ {e['error'][:90]}")

    for cid in sorted(analysis):
        r = analysis[cid]
        tuples = r["tuples"]
        aspect_dist = Counter(t["aspect"] for t in tuples)
        pol_dist = Counter(t["polarity"] for t in tuples)
        ch_dist = Counter(t["channel"] for t in tuples)
        n_sugg = sum(1 for t in tuples if t.get("is_suggestion"))
        print(f"\n■ {cid} — tuple {len(tuples)}건 "
              f"(표본 {r['sample_size']} · 가드 제거 {r['dropped_by_guard']})")
        print(f"  polarity {dict(pol_dist)} · channel {dict(ch_dist)} · suggestion {n_sugg}")
        for a, n in aspect_dist.most_common():
            print(f"    {a:42s} {n}")
        for t in tuples[:3]:
            print(f"  · [{t['aspect']}/{t['polarity']}{t['intensity']}] «{t['quote'][:55]}»")
        (out_dir / f"{cid}.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

    # aspect 커버리지 종합 (RI-D4 조정 판단 근거)
    all_aspects = {a.get("aspect_id") for a in
                   taxonomy["report_config"]["reaction_insight"].get("aspect_codebook", [])}
    covered = {t["aspect"] for r in analysis.values() for t in r["tuples"]}
    print(f"\n[aspect 커버리지] {len(covered)}/{len(all_aspects)} "
          f"— 미커버: {sorted(all_aspects - covered) or '없음'}")
    print(f"→ 저장: {out_dir.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "community":
        raise SystemExit(run_community())
    if cmd == "youtube":
        raise SystemExit(run_youtube())
    if cmd == "absa":
        raise SystemExit(run_absa())
    print(__doc__)
    raise SystemExit(1)
