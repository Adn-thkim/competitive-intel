"""
test_official_content_collection_step2.py
------------------------------------------
official_content_collection 노드 Step 2 (run_llm_extraction) 단위 테스트.

설계 근거: docs/design/feature_extraction_node_design.md §5-3 (FE-D4·FE-D8)
검증 목표: §9-3 — 캐시 결정성 (동일 입력 2회차 LLM 호출 0건, 본문 변경 시 cache miss)
LLM·네트워크 호출 없음 — analyzer 는 fake 주입, _fetch_content 는 monkeypatch.

실행: python -m pytest test_official_content_collection_step2.py -q
"""

import copy
import json

import pytest
from jsonschema import validate

import server.graph.agent_cache as agent_cache
import server.graph.nodes.official_content_collection_node as occ
from server.graph.nodes.official_content_collection_node import (
    _build_keyword_pool,
    _features_meta,
    _load_llm_assets,
    run_llm_extraction,
)
from test_official_content_collection_step0 import _base_state

_, _OUTPUT_SCHEMA = _load_llm_assets()


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_cache, "AGENT_OUTPUT_CACHE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _fake_fetch(monkeypatch):
    """네트워크 차단 — 모든 URL 이 동일 본문으로 ok 반환 (테스트별 content 교체 가능)."""
    state = {"content": "환전 수수료는 무료입니다. " * 30}

    def _fake(url):
        return {"url": url, "fetch_status": "ok",
                "content": state["content"], "error": ""}

    monkeypatch.setattr(occ, "_fetch_content", _fake)
    return state


class FakeAnalyzer:
    """call_with_schema 인터페이스 — payload 의 features 전체에 explicit 값 반환."""
    model = "fake-model-v1"

    def __init__(self):
        self.calls = 0
        self.last_payload = None

    def call_with_schema(self, prompt: str, output_schema: dict) -> dict:
        self.calls += 1
        payload = json.loads(prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
        self.last_payload = payload
        return {
            "candidate_id": payload["candidate_id"],
            "extracted_features": [
                {"feature_id": f["feature_id"], "value": "무료",
                 "value_numeric": 0, "unit": "%", "as_of": "",
                 "extraction_status": "explicit",
                 "evidence": "환전 수수료는 무료입니다.",
                 "source_url": payload["pages"][0]["url"], "confidence": 0.9,
                 "is_promotional": False, "valid_until": ""}
                for f in payload["features"]
            ],
            "profile_summary": f"{payload['candidate_name']} 요약",
            "conflicts": [],
        }


class TestRunLlmExtraction:
    def test_payload_contract_and_schema_valid_output(self):
        """payload 가 input 계약을 지키고, 출력이 output.schema 를 만족."""
        analyzer = FakeAnalyzer()
        results, errors, stats = run_llm_extraction(_base_state(), analyzer=analyzer)
        assert not errors
        comp = next(r for r in results if r["candidate_id"] == "comp_travel_wallet")
        validate(comp["output"], _OUTPUT_SCHEMA)
        # payload: 이 candidate 의 feature 만 포함 + ok 페이지만 포함
        payload = analyzer.last_payload
        assert [f["feature_id"] for f in payload["features"]] == ["feat_exchange_fee"]
        assert payload["report_type"] == "comparison_matrix"
        assert all(p["excerpt"] for p in payload["pages"])

    def test_no_content_candidate_skips_llm(self):
        """URL 0건(coverage not_found) candidate 는 LLM 미호출 + no_content 마커 (§7)."""
        analyzer = FakeAnalyzer()
        results, _errors, stats = run_llm_extraction(_base_state(), analyzer=analyzer)
        own = next(r for r in results if r["candidate_id"] == "own_toss_travel")
        assert own["no_content"] is True and own["output"] is None
        assert "own_toss_travel" in stats["skipped_no_pages"]
        assert analyzer.calls == 1                # comp 1건만 호출

    def test_cache_hit_second_run(self):
        """§9-3: 동일 입력 2회차 — LLM 호출 0건 + 결과 동일."""
        a1, a2 = FakeAnalyzer(), FakeAnalyzer()
        r1, _, s1 = run_llm_extraction(_base_state(), analyzer=a1)
        r2, _, s2 = run_llm_extraction(_base_state(), analyzer=a2)
        assert s1["llm_calls"] == 1 and s2["llm_calls"] == 0
        assert s2["cache_hits"] == 1 and a2.calls == 0
        out1 = next(r for r in r1 if r["output"])["output"]
        out2 = next(r for r in r2 if r["output"])["output"]
        assert out1 == out2

    def test_content_change_invalidates_cache(self, _fake_fetch):
        """§9-3: 페이지 본문 변경 → 발췌 해시 변경 → cache miss."""
        a1, a2 = FakeAnalyzer(), FakeAnalyzer()
        run_llm_extraction(_base_state(), analyzer=a1)
        _fake_fetch["content"] = "환전 수수료는 0.5%로 변경되었습니다. " * 30
        _, _, s2 = run_llm_extraction(_base_state(), analyzer=a2)
        assert s2["llm_calls"] == 1 and a2.calls == 1

    def test_feature_set_change_invalidates_cache(self):
        """선택 feature 집합 변경 → cache miss (캐시 키에 feature_ids 포함)."""
        run_llm_extraction(_base_state(), analyzer=FakeAnalyzer())
        state = _base_state()
        state["selected_feature_ids"] = ["feat_exchange_fee", "feat_unselected"]
        a2 = FakeAnalyzer()
        _, _, s2 = run_llm_extraction(state, analyzer=a2)
        assert s2["llm_calls"] == 1

    def test_analyzer_failure_is_partial(self):
        """analyzer 예외 → 해당 candidate 만 output=None + errors 누적, 전체 진행 (§7)."""
        class BoomAnalyzer(FakeAnalyzer):
            def call_with_schema(self, prompt, output_schema):
                raise RuntimeError("API 5xx")

        results, errors, _ = run_llm_extraction(_base_state(), analyzer=BoomAnalyzer())
        comp = next(r for r in results if r["candidate_id"] == "comp_travel_wallet")
        assert comp["output"] is None
        assert len(errors) == 1 and "comp_travel_wallet" in errors[0]["error"]

    def test_only_candidates_filter(self):
        """프로파일링용 only_candidates 제한."""
        results, _, _ = run_llm_extraction(
            _base_state(), analyzer=FakeAnalyzer(),
            only_candidates={"comp_travel_wallet"})
        assert [r["candidate_id"] for r in results] == ["comp_travel_wallet"]


class TestKeywordPool:
    def test_pool_composition_and_determinism(self):
        state = _base_state()
        metas = _features_meta(state)
        pool = _build_keyword_pool(state, metas)
        assert "수수료" in pool and "약관" in pool      # 정적 7종
        assert "환전" in pool                           # feature_name 토큰
        assert pool == sorted(pool)                     # 정렬 = 결정론
        assert pool == _build_keyword_pool(copy.deepcopy(state), metas)
