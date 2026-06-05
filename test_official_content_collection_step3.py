"""
test_official_content_collection_step3.py
------------------------------------------
official_content_collection 노드 Step 3 (assemble_feature_pool + 노드 main) 단위 테스트.

설계 근거: docs/design/feature_extraction_node_design.md §5-4 · §6 (FE-D12)
검증 목표: §9-4 — feature_pool 이 (선택 feature × candidate) 셀을 모두 보유
(값 또는 상태값), 부분 실패 시 not_found 행 유지 (§9-5).

실행: python -m pytest test_official_content_collection_step3.py -q
"""

import pytest

import server.graph.agent_cache as agent_cache
import server.graph.nodes.official_content_collection_node as occ
from server.graph.nodes.official_content_collection_node import (
    assemble_feature_pool,
    official_content_collection_node,
)
from test_official_content_collection_step0 import _base_state
from test_official_content_collection_step2 import FakeAnalyzer


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_cache, "AGENT_OUTPUT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        occ, "_fetch_content",
        lambda url: {"url": url, "fetch_status": "ok",
                     "content": "환전 수수료는 무료입니다. " * 30, "error": ""})


def _targets_and_results(analyzer=None):
    state = _base_state()
    analyzer = analyzer or FakeAnalyzer()
    results, errors, stats = occ.run_llm_extraction(state, analyzer=analyzer)
    return results, errors, stats["targets"]


# ─── assemble_feature_pool (§6-1 · §6-2) ─────────────────────────────────────

class TestAssemble:
    def test_no_missing_cells(self):
        """§9-4: 모든 (feature × candidate) 셀이 값 또는 상태값을 보유."""
        results, _, targets = _targets_and_results()
        pool, _profiles = assemble_feature_pool(results, targets)
        for t in targets:
            for fid in t["feature_ids"]:
                cell = pool[fid][t["candidate_id"]]
                assert cell["extraction_status"], f"{fid}×{t['candidate_id']} 상태값 없음"

    def test_pivot_and_field_preservation(self):
        """2단계 키(feature → candidate) + FE-D12 필드·source_origin 보존."""
        results, _, targets = _targets_and_results()
        pool, _ = assemble_feature_pool(results, targets)
        cell = pool["feat_exchange_fee"]["comp_travel_wallet"]
        assert cell["value"] == "무료" and cell["extraction_status"] == "explicit"
        assert cell["is_promotional"] is False and cell["valid_until"] == ""
        # source_url 이 target urls 중 하나 → origin 역매핑 성공
        assert cell["source_origin"] in ("official_source", "official_subpage",
                                         "additional_validated")

    def test_no_content_candidate_filled_not_found(self):
        """§9-5: own(coverage not_found, URL 0건) 은 전 feature not_found 행 유지."""
        results, _, targets = _targets_and_results()
        pool, profiles = assemble_feature_pool(results, targets)
        cell = pool["feat_exchange_fee"]["own_toss_travel"]
        assert cell["extraction_status"] == "not_found" and cell["confidence"] == 0
        own_profile = next(p for p in profiles if p["candidate_id"] == "own_toss_travel")
        assert own_profile["needs_manual_review"] is True   # output 없음

    def test_llm_failure_candidate_filled_not_found(self):
        """LLM 실패 candidate 도 not_found 행으로 유지 (§7)."""
        class BoomAnalyzer(FakeAnalyzer):
            def call_with_schema(self, prompt, output_schema):
                raise RuntimeError("API down")

        results, errors, targets = _targets_and_results(analyzer=BoomAnalyzer())
        pool, profiles = assemble_feature_pool(results, targets)
        assert pool["feat_exchange_fee"]["comp_travel_wallet"]["extraction_status"] == "not_found"
        assert errors  # 부분 실패 기록

    def test_needs_manual_review_threshold(self):
        """explicit 비율 < 50% 또는 conflicts 존재 시 수동 검토 플래그."""
        class PartialAnalyzer(FakeAnalyzer):
            def call_with_schema(self, prompt, output_schema):
                out = super().call_with_schema(prompt, output_schema)
                for f in out["extracted_features"]:
                    f["extraction_status"] = "partial"
                return out

        results, _, targets = _targets_and_results(analyzer=PartialAnalyzer())
        _, profiles = assemble_feature_pool(results, targets)
        comp = next(p for p in profiles if p["candidate_id"] == "comp_travel_wallet")
        assert comp["needs_manual_review"] is True


# ─── 노드 main (Step 0~3 통합) ───────────────────────────────────────────────

class TestNodeMain:
    def test_full_run_writes_feature_pool(self, monkeypatch, tmp_path):
        original_run = occ.run_llm_extraction  # 패치 전 원본 캡처 (재귀 방지)
        monkeypatch.setattr(occ, "run_llm_extraction",
                            lambda state, analyzer=None, only_candidates=None:
                            original_run(state, analyzer=FakeAnalyzer()))
        # 관측성 저장을 tmp 로 우회
        import server.config as cfg
        monkeypatch.setattr(cfg, "BASE_DIR", tmp_path)

        out = official_content_collection_node(_base_state(), config={
            "configurable": {"thread_id": "test-run-1"}})
        assert "feature_pool" in out and out["feature_pool"]
        assert out["agent_steps"][0]["status"] == "completed"
        assert len(out["product_profiles"]) == 2
        # §6-4 관측성: 조립 결과가 run_id 경로에 저장됨
        obs_dir = tmp_path / "data" / "collection" / "official_content_collection" / "test-run-1"
        assert (obs_dir / "feature_pool.json").exists()
        assert (obs_dir / "product_profiles.json").exists()

    def test_skip_when_report_not_selected(self):
        state = _base_state()
        state["selected_purposes"] = ["battlecard"]
        out = official_content_collection_node(state)
        assert out["agent_steps"][0]["status"] == "skipped"
        assert "feature_pool" not in out
