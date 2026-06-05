"""
test_official_content_collection_step1.py
------------------------------------------
official_content_collection 노드 Step 1 (_fetch_content + _build_excerpt) 단위 테스트.

설계 근거: docs/design/feature_extraction_node_design.md §5-2 (콘텐츠 수집) ·
§5-2a (키워드 근접 발췌, FE-D9) · FE-D10 (Trafilatura + BS4 폴백)
검증 목표: §9-2 (본문 추출·SPA 분류·폴백) · §9-2a (발췌 결정론·헤더 보존·후반부 키워드)

네트워크를 호출하지 않는다 — requests.get은 전부 monkeypatch.
실행: python -m pytest test_official_content_collection_step1.py -q
"""

import pytest

import server.graph.agent_cache as agent_cache
import server.graph.nodes.official_content_collection_node as occ
from server.graph.nodes.official_content_collection_node import (
    _CANDIDATE_EXCERPT_BUDGET,
    _EXCERPT_OMIT_MARKER,
    _PAGE_EXCERPT_BUDGET,
    _SPA_MIN_CHARS,
    _build_excerpt,
    _extract_html_text,
    _extract_pdf_text,
    _fetch_content,
    _page_excerpt_budget,
)


# ─── 공용 fixture ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """fetch 캐시가 저장소의 data/cache 를 오염시키지 않도록 임시 경로로 격리."""
    monkeypatch.setattr(agent_cache, "AGENT_OUTPUT_CACHE_DIR", tmp_path)


class _FakeResponse:
    def __init__(self, text="", content=b"", status_code=200,
                 content_type="text/html; charset=utf-8"):
        self.text = text
        self.content = content or text.encode("utf-8")
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}


_PARA = "트래블카드 비교를 위한 본문 단락입니다. 해외 결제와 환전 기능을 설명합니다. "

_HTML_WITH_TABLE = f"""
<html><head><title>수수료 안내</title><script>var x=1;</script></head>
<body><nav>메뉴 네비게이션</nav>
<h1>수수료 안내</h1>
<p>{_PARA * 8}</p>
<table>
  <tr><th>항목</th><th>수수료율</th></tr>
  <tr><td>환전 수수료</td><td>0.5%</td></tr>
  <tr><td>해외 결제 수수료</td><td>무료</td></tr>
</table>
<p>{_PARA * 8}</p>
<footer>회사 정보 푸터</footer></body></html>
"""

def _make_minimal_pdf() -> bytes:
    """xref 오프셋을 정확히 계산한 최소 유효 PDF (본문 'Fee 0.5 percent ...')."""
    stream = b"BT /F1 12 Tf 72 720 Td (Fee 0.5 percent applies to exchange) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref_pos))
    return bytes(out)


_MINIMAL_PDF = _make_minimal_pdf()


# ─── §9-2: 본문 추출 ─────────────────────────────────────────────────────────

class TestExtractHtml:
    def test_trafilatura_extracts_table_and_drops_boilerplate(self):
        text = _extract_html_text(_HTML_WITH_TABLE)
        assert "0.5%" in text                    # 수수료표 값 보존 (FE-D10 핵심)
        assert "var x=1" not in text             # <script> 제거
        assert len(text) >= _SPA_MIN_CHARS

    def test_bs4_fallback_when_trafilatura_fails(self, monkeypatch):
        monkeypatch.setattr(occ.trafilatura, "extract",
                            lambda *a, **k: None)
        text = _extract_html_text(_HTML_WITH_TABLE)
        assert "0.5%" in text                    # 폴백 경로에서도 본문 확보
        assert "var x=1" not in text


class TestExtractPdf:
    def test_minimal_pdf_text(self):
        text = _extract_pdf_text(_MINIMAL_PDF)
        assert "Fee 0.5 percent" in text


# ─── §9-2: _fetch_content — SPA 분류 · 캐시 · 실패 처리 ──────────────────────

class TestFetchContent:
    def test_spa_classified_and_excluded(self, monkeypatch):
        spa_html = "<html><body><div id='root'></div><p>로딩 중</p></body></html>"
        monkeypatch.setattr(occ.requests, "get",
                            lambda *a, **k: _FakeResponse(text=spa_html))
        result = _fetch_content("https://spa.example.com/app")
        assert result["fetch_status"] == "requires_dynamic_render"

    def test_ok_fetch_is_cached_24h(self, monkeypatch):
        calls = {"n": 0}

        def _fake_get(*a, **k):
            calls["n"] += 1
            return _FakeResponse(text=_HTML_WITH_TABLE)

        monkeypatch.setattr(occ.requests, "get", _fake_get)
        r1 = _fetch_content("https://bank.example.com/fees")
        r2 = _fetch_content("https://bank.example.com/fees")
        assert calls["n"] == 1                    # 2회차는 캐시 적중
        assert r1["fetch_status"] == "ok" == r2["fetch_status"]
        assert r1["content"] == r2["content"]

    def test_fetch_failure_not_cached(self, monkeypatch):
        calls = {"n": 0}

        def _fake_get(*a, **k):
            calls["n"] += 1
            raise occ.requests.exceptions.ConnectionError("boom")

        monkeypatch.setattr(occ.requests, "get", _fake_get)
        r1 = _fetch_content("https://down.example.com")
        r2 = _fetch_content("https://down.example.com")
        assert r1["fetch_status"] == "fetch_failed"
        assert calls["n"] == 2                    # 실패는 캐시하지 않음 (재시도 허용)

    def test_http_error_status_is_failure(self, monkeypatch):
        monkeypatch.setattr(occ.requests, "get",
                            lambda *a, **k: _FakeResponse(text="not found",
                                                          status_code=404))
        assert _fetch_content("https://x.example.com/404")["fetch_status"] == "fetch_failed"

    def test_pdf_content_type_routes_to_pdf_parser(self, monkeypatch):
        monkeypatch.setattr(
            occ.requests, "get",
            lambda *a, **k: _FakeResponse(content=_MINIMAL_PDF,
                                          content_type="application/pdf"),
        )
        result = _fetch_content("https://bank.example.com/terms")  # 확장자 없이 Content-Type 판정
        assert result["fetch_status"] in ("ok", "requires_dynamic_render")
        assert "Fee 0.5 percent" in result["content"]


# ─── §9-2a: _build_excerpt — 발췌 ────────────────────────────────────────────

def _long_doc() -> str:
    """20,000자 규모 — 헤더·중간 헤딩·문서 끝 키워드 문장을 가진 합성 문서."""
    filler = "일반 안내 문장입니다. 카드 이용 시 유의사항을 확인하세요. "
    return (
        "# 트래블카드 이용 약관 (2026-05 개정)\n"
        + filler * 150
        + "\n## 제5조 부가 혜택\n"
        + filler * 150
        + "\n결제망 사정에 따라 일부 가맹점 제한이 있습니다.\n"
        + filler * 100
        + "\n환전 수수료는 0.5%가 적용되며 재환전 시 면제됩니다.\n"
    )


class TestBuildExcerpt:
    def test_short_content_passthrough(self):
        content = "짧은 본문입니다."
        assert _build_excerpt(content, ["수수료"], budget=6000) == content

    def test_tail_keyword_window_included(self):
        doc = _long_doc()
        excerpt = _build_excerpt(doc, ["수수료"], budget=3000)
        assert len(excerpt) <= 3000
        assert "환전 수수료는 0.5%" in excerpt        # 문서 끝(>1.5만자 지점) 키워드 생존
        assert excerpt.startswith("# 트래블카드 이용 약관")  # 헤더 항상 포함
        assert _EXCERPT_OMIT_MARKER.strip() in excerpt   # 생략 구간 마커

    def test_heading_lines_survive_without_keyword(self):
        doc = _long_doc()
        excerpt = _build_excerpt(doc, ["존재하지않는키워드"], budget=2500)
        assert "## 제5조 부가 혜택" in excerpt           # 헤딩은 키워드 무관 보존

    def test_deterministic(self):
        doc = _long_doc()
        kws = ["수수료", "혜택", "한도"]
        assert _build_excerpt(doc, kws, budget=3000) == _build_excerpt(doc, kws, budget=3000)
        assert _build_excerpt(doc, list(reversed(kws)), budget=3000) == \
               _build_excerpt(doc, kws, budget=3000)    # 키워드 순서 무관

    def test_budget_never_exceeded(self):
        doc = _long_doc()
        for budget in (1000, 2142, 6000):
            assert len(_build_excerpt(doc, ["수수료", "혜택"], budget=budget)) <= budget


class TestPageBudget:
    def test_allocation_rule(self):
        """FE-D5 v3: 페이지당 예산 = min(6,000, 30,000 / 페이지 수)."""
        assert _page_excerpt_budget(3) == _PAGE_EXCERPT_BUDGET            # 30000/3=10000 > 6000
        assert _page_excerpt_budget(14) == _CANDIDATE_EXCERPT_BUDGET // 14  # 2142
        assert _page_excerpt_budget(0) == _PAGE_EXCERPT_BUDGET            # 방어적 기본값
