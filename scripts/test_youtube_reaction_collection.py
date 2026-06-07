"""
test_youtube_reaction_collection.py
------------------------------------
youtube_reaction_collection 노드 단위 테스트 (RI-D1~D4).

설계 근거: docs/design/reaction_insight_node_design.md §3 · §8-1·2
네트워크 비호출 — youtube_client 함수는 monkeypatch.

실행: python -m pytest scripts/test_youtube_reaction_collection.py -q
"""

import pytest

import server.graph.nodes.youtube_reaction_collection_node as yrc
from server.graph.nodes.youtube_reaction_collection_node import (
    _COMMENTS_MIN_PER_VIDEO,
    _COMMENTS_PER_CANDIDATE,
    _COMMENTS_PER_VIDEO,
    _VIDEOS_PER_CANDIDATE,
    cap_candidate_comments,
    filter_comments,
    select_videos,
    youtube_reaction_collection_node,
)
from server.llm.youtube_client import YouTubeQuotaExceeded


def _yt_url(vid):
    return {"url": f"https://www.youtube.com/watch?v={vid}",
            "origin": "youtube_reactions", "video_id": vid}


def _feature(fid, cid, videos):
    """videos: [(video_id, view_count)]"""
    return {
        "report_type": "reaction_insight", "feature_id": fid,
        "feature_name": fid, "description": "", "priority": "high",
        "candidate_coverage": [{
            "candidate_id": cid, "coverage": "sufficient",
            "existing_urls": [
                {**_yt_url(vid), "view_count": vc} for vid, vc in videos
            ],
            "additional_urls": [],
        }],
    }


def _base_state():
    return {
        "selected_purposes": ["reaction_insight"],
        "selected_feature_ids": ["feat_fee", "feat_ux"],
        "analysis_features": [
            _feature("feat_fee", "own_x",
                     [("vidA0000000", 9000), ("vidB0000000", 5000), ("vidC0000000", 100)]),
            _feature("feat_ux", "own_x",
                     [("vidB0000000", 5000), ("vidD0000000", 300)]),
        ],
    }


# ─── §8-1: 영상 선별 ─────────────────────────────────────────────────────────

class TestSelectVideos:
    def test_top2_per_feature_and_dedup(self):
        sel = select_videos(_base_state())
        vids = [v["video_id"] for v in sel["own_x"]]
        # feat_fee 상위 2 = A·B, feat_ux 상위 2 = B·D → union A·B·D (C 탈락)
        assert set(vids) == {"vidA0000000", "vidB0000000", "vidD0000000"}
        # 중복 영상 B 는 feature_ids 병합
        b = next(v for v in sel["own_x"] if v["video_id"] == "vidB0000000")
        assert b["feature_ids"] == ["feat_fee", "feat_ux"]

    def test_candidate_cap_with_feature_coverage(self):
        """상한 6 초과 시 feature 커버리지 우선 절단."""
        state = _base_state()
        # feat_fee 에 고조회수 영상 6개 추가 → 후보 풀 9개
        state["analysis_features"][0]["candidate_coverage"][0]["existing_urls"] += [
            {**_yt_url(f"vidE{i}000000"[:11]), "view_count": 8000 - i}
            for i in range(6)
        ]
        # feat_fee 상위 2 는 A(9000)·E0(8000) — 그래도 feat_ux 의 영상이 보장돼야 함
        sel = select_videos(state)
        vids = {v["video_id"] for v in sel["own_x"]}
        assert len(vids) <= _VIDEOS_PER_CANDIDATE
        assert vids & {"vidB0000000", "vidD0000000"}, "feat_ux 커버 영상이 전부 탈락"

    def test_filters_origin_and_selection(self):
        state = _base_state()
        state["analysis_features"][0]["candidate_coverage"][0]["existing_urls"].append(
            {"url": "https://blog.naver.com/x", "origin": "blog_community",
             "view_count": 99999})
        sel = select_videos(state)
        assert all(v["video_id"].startswith("vid") for v in sel["own_x"])

    def test_gate_when_report_not_selected(self):
        state = _base_state()
        state["selected_purposes"] = ["comparison_matrix"]
        assert select_videos(state) == {}


# ─── §8-1: 댓글 필터·상한 ────────────────────────────────────────────────────

def _comment(i, text, likes):
    return {"comment_id": f"c{i:04d}", "text": text, "like_count": likes,
            "published_at": "2026-06-01T00:00:00Z", "author_hash": "ab"}


class TestCommentFilter:
    def test_short_emoji_duplicate_removed_and_top_by_likes(self):
        raw = (
            [_comment(i, f"실사용 후기를 자세히 남깁니다 {i}", likes=i) for i in range(40)]
            + [_comment(900, "굿", 999),                      # 10자 미만
               _comment(901, "👍👍👍👍👍👍👍👍👍👍👍", 999),     # 이모지 전용
               _comment(902, "실사용 후기를 자세히 남깁니다 39", 999)]  # 중복 텍스트
        )
        kept = filter_comments(raw)
        assert len(kept) == _COMMENTS_PER_VIDEO
        assert kept[0]["like_count"] >= kept[-1]["like_count"]   # 좋아요 내림차순
        assert all(len(c["text"]) >= 10 for c in kept)

    def test_noise_patterns_removed(self):
        """RI-D10: ㅋㅋㅋ·단순 감탄·영상 자체 언급·'N등' 제거 (오탐 방지 — 제품 의견은 유지)."""
        raw = [
            _comment(1, "ㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋ", 50),                  # 순수 filler (길이 무관)
            _comment(2, "영상 잘 봤습니다 감사합니다", 40),                   # 영상 자체 언급
            _comment(3, "1등!", 30),
            _comment(4, "최고예요~~", 20),
            _comment(5, "영상 보고 바로 발급했는데 환전 수수료 진짜 없네요", 0),  # 유지돼야 함
            _comment(6, "수수료 무료라더니 재환전은 빼는 거 ㅋㅋㅋ 아쉽네요", 0),  # 유지 (잡담 아님)
        ]
        kept = filter_comments(raw)
        texts = [c["text"] for c in kept]
        assert texts == [
            "영상 보고 바로 발급했는데 환전 수수료 진짜 없네요",
            "수수료 무료라더니 재환전은 빼는 거 ㅋㅋㅋ 아쉽네요",
        ] or set(texts) == {
            "영상 보고 바로 발급했는데 환전 수수료 진짜 없네요",
            "수수료 무료라더니 재환전은 빼는 거 ㅋㅋㅋ 아쉽네요",
        }

    def test_two_tier_order_zero_likes_by_recency(self):
        """RI-D10: 좋아요 0 구간은 최신순 — 좋아요 구간 뒤에 배치."""
        raw = (
            [_comment(i, f"좋아요 받은 충분히 긴 실사용 의견 {i}", likes=5 - i) for i in range(3)]
            + [{**_comment(10 + i, f"좋아요 없는 충분히 긴 최근 의견 {i}", 0),
                "published_at": f"2026-06-0{i + 1}T00:00:00Z"} for i in range(3)]
        )
        kept = filter_comments(raw)
        assert [c["like_count"] for c in kept[:3]] == [5, 4, 3]          # 좋아요 구간 우선
        zero_dates = [c["published_at"] for c in kept[3:]]
        assert zero_dates == sorted(zero_dates, reverse=True)             # 최신순

    def test_candidate_cap_with_min_per_video(self):
        """6영상 × 30건 = 180 → 150 절단, 영상당 최소 15건 보장."""
        by_video = {
            f"vid{chr(65 + i)}": [
                # i 번 영상의 댓글 30건 — 영상별 좋아요 규모 차등 (i=0 이 가장 낮음)
                _comment(i * 100 + j, f"영상{i} 의 충분히 긴 사용 후기 {j}", likes=i * 50 + (30 - j))
                for j in range(30)
            ]
            for i in range(6)
        }
        capped = cap_candidate_comments(by_video)
        assert len(capped) == _COMMENTS_PER_CANDIDATE
        # 좋아요가 가장 낮은 vidA 도 최소 보장분 유지
        vid_a = [c for c in capped if c["comment_id"].startswith("c00")]
        assert len(vid_a) >= _COMMENTS_MIN_PER_VIDEO


# ─── §8-2: 노드 통합 (mock client) ───────────────────────────────────────────

class TestNode:
    @pytest.fixture(autouse=True)
    def _mock_client(self, monkeypatch):
        self.comment_calls = []
        monkeypatch.setattr(
            yrc, "youtube_videos_statistics",
            lambda ids: {v: {"view_count": 0, "like_count": 0, "comment_count": 0}
                         for v in ids})
        monkeypatch.setattr(
            yrc, "youtube_videos_snippet",
            lambda ids: {v: {"title": f"제목 {v}", "description": "설명",
                             "published_at": "", "channel_title": "채널"} for v in ids})
        monkeypatch.setattr(
            yrc, "youtube_comment_threads",
            lambda vid: self.comment_calls.append(vid) or [
                _comment(i, f"{vid} 에 대한 충분히 긴 후기 {i}", likes=i) for i in range(40)
            ])

    def test_full_collection(self):
        out = youtube_reaction_collection_node(_base_state())
        assert out["agent_steps"][0]["status"] == "completed"
        assert {v["video_id"] for v in out["collected_videos"]} == \
               {"vidA0000000", "vidB0000000", "vidD0000000"}
        assert all(v["title"] for v in out["collected_videos"])      # snippet 보강
        assert len(out["selected_comments"]) == 3 * _COMMENTS_PER_VIDEO
        assert all(c["candidate_id"] == "own_x" for c in out["selected_comments"])
        # 실측 발견 버그 회귀 — 댓글마다 영상 연관(video_id) 보존
        assert all(c["video_id"] for c in out["selected_comments"])

    def test_stats_enrichment_drives_selection(self, monkeypatch):
        """RI-D9: mapping 메타의 view_count 가 전부 0 이어도 statistics 보강으로
        조회수 정렬이 동작한다 (실측 발견 — 조회수 전부 0 문제)."""
        state = _base_state()
        for feat in state["analysis_features"]:
            for cov in feat["candidate_coverage"]:
                for u in cov["existing_urls"]:
                    u["view_count"] = 0          # mapping carry 손실 재현
        monkeypatch.setattr(
            yrc, "youtube_videos_statistics",
            lambda ids: {"vidA0000000": {"view_count": 100},
                         "vidB0000000": {"view_count": 9_000},
                         "vidC0000000": {"view_count": 8_000},
                         "vidD0000000": {"view_count": 50}})
        out = youtube_reaction_collection_node(state)
        vids = {v["video_id"] for v in out["collected_videos"]}
        # feat_fee 상위 2 = B(9000)·C(8000) — 보강 없이는 사전순 A·B 가 됐을 것
        assert "vidC0000000" in vids and "vidB0000000" in vids

    def test_quota_exceeded_partial(self, monkeypatch):
        def _boom(vid):
            raise YouTubeQuotaExceeded("limit")
        monkeypatch.setattr(yrc, "youtube_comment_threads", _boom)
        out = youtube_reaction_collection_node(_base_state())
        assert out["agent_steps"][0]["status"] == "completed"        # 부분 실패 허용
        assert out["collected_videos"]                                # 영상 메타는 유지
        assert out["selected_comments"] == []
        assert any("quota" in e["error"] for e in out["errors"])

    def test_skip_when_report_not_selected(self):
        state = _base_state()
        state["selected_purposes"] = ["comparison_matrix"]
        out = youtube_reaction_collection_node(state)
        assert out["agent_steps"][0]["status"] == "skipped"
        assert "collected_videos" not in out
