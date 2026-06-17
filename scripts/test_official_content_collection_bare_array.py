"""
test_official_content_collection_bare_array.py
-----------------------------------------------
회귀 테스트: 모델이 래퍼 객체를 생략하고 extracted_features 배열만 단독 반환하는
구조 일탈(bare array)을 노드 레벨 repair 훅(_wrap_bare_features)이 정규 객체로
복구하는지 검증한다.

배경: official_content_collection_node step2 에서 ClaudeApiAnalyzer 가 bare array
응답을 받아 schema 검증에 3회 모두 실패 → candidate output=None → feature_pool
전 셀 not_found(매트릭스 열 공백) 가 되던 버그의 회귀 방지.

실행: python -m pytest scripts/test_official_content_collection_bare_array.py -q
"""

import json
import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import server.llm.claude_api_analyzer as mod
from server.config import AGENTS_DIR
from server.llm.claude_api_analyzer import ClaudeApiAnalyzer
from server.graph.nodes.official_content_collection_node import _wrap_bare_features

_SCHEMA = json.loads(
    (AGENTS_DIR / "official_content_collection" / "output.schema.json").read_text("utf-8")
)


def _valid_item(fid="feat_overseas_payment_fee_rate"):
    return {
        "feature_id": fid, "value": "무료", "value_numeric": 0, "unit": "%",
        "as_of": "", "extraction_status": "explicit", "evidence": "수수료 무료",
        "source_url": "https://example.com", "confidence": 0.9,
        "is_promotional": False, "valid_until": "",
    }


def _analyzer(monkeypatch, invoke_returns):
    monkeypatch.setattr(mod.anthropic, "Anthropic", lambda **k: object())
    a = ClaudeApiAnalyzer(model="claude-sonnet-4-6")
    monkeypatch.setattr(a, "_invoke_api_with_tool", lambda prompt, schema: invoke_returns)
    return a


class TestWrapHelper:
    def test_wraps_bare_list(self):
        out = _wrap_bare_features("own_토스트래블카드")([_valid_item()])
        assert out["candidate_id"] == "own_토스트래블카드"
        assert out["extracted_features"][0]["feature_id"] == "feat_overseas_payment_fee_rate"
        assert out["profile_summary"] == "" and out["conflicts"] == []

    def test_passes_object_through_unchanged(self):
        obj = {"candidate_id": "own_x"}
        assert _wrap_bare_features("own_x")(obj) is obj


class TestRepairInAnalyzer:
    def test_bare_array_of_valid_items_recovered(self, monkeypatch):
        """bare array(유효 항목) → repair 가 1회 시도에 통과시킨다."""
        a = _analyzer(monkeypatch, [_valid_item(), _valid_item("feat_exchange_rate_application_method")])
        out = a.call_with_schema(
            "p", _SCHEMA, repair=_wrap_bare_features("own_토스트래블카드"))
        assert out["candidate_id"] == "own_토스트래블카드"
        assert {f["feature_id"] for f in out["extracted_features"]} == {
            "feat_overseas_payment_fee_rate", "feat_exchange_rate_application_method"}

    def test_item_violating_schema_still_fails(self, monkeypatch):
        """배열 항목이 feat_ 패턴을 위반하면 wrap 후에도 validate 가 실패한다(날조 금지)."""
        bad = _valid_item(fid="overseas_fee")  # feat_ 접두 누락 → pattern 위반
        a = _analyzer(monkeypatch, [bad])
        with pytest.raises(RuntimeError):
            a.call_with_schema(
                "p", _SCHEMA, max_retries=1,
                repair=_wrap_bare_features("own_토스트래블카드"))

    def test_proper_object_passes_without_repair(self, monkeypatch):
        obj = {"candidate_id": "own_토스트래블카드",
               "extracted_features": [_valid_item()],
               "profile_summary": "요약", "conflicts": []}
        a = _analyzer(monkeypatch, obj)
        assert a.call_with_schema("p", _SCHEMA) == obj

    def test_json_string_encoded_array_recovered(self, monkeypatch):
        """실제 버그 재현: 모델이 출력 전체를 들여쓰기된 JSON 문자열로 반환한 경우.

        어댑터 _fix_string_encoded_fields 가 최상위 문자열을 배열로 디코딩 →
        repair 가 정규 객체로 wrap → 통과. (str 단계에서 repair(list-only)만 있던
        이전 수정으로는 잡히지 않던 케이스.)
        """
        encoded = json.dumps([_valid_item()], ensure_ascii=False, indent=2)
        assert isinstance(encoded, str)
        a = _analyzer(monkeypatch, encoded)
        out = a.call_with_schema(
            "p", _SCHEMA, repair=_wrap_bare_features("own_토스트래블카드"))
        assert out["candidate_id"] == "own_토스트래블카드"
        assert out["extracted_features"][0]["feature_id"] == "feat_overseas_payment_fee_rate"

    def test_json_string_encoded_object_recovered(self, monkeypatch):
        """모델이 정규 객체를 JSON 문자열로 직렬화한 경우도 디코딩되어 통과."""
        obj = {"candidate_id": "own_토스트래블카드",
               "extracted_features": [_valid_item()],
               "profile_summary": "요약", "conflicts": []}
        a = _analyzer(monkeypatch, json.dumps(obj, ensure_ascii=False))
        out = a.call_with_schema(
            "p", _SCHEMA, repair=_wrap_bare_features("own_토스트래블카드"))
        assert out["profile_summary"] == "요약"
