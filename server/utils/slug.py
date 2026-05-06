"""
slug.py — 상품 식별자(product_id) 생성 유틸리티
------------------------------------------------
사용자 입력(raw 또는 LLM 1차 정규화된 상품명)을 받아
최종 product_id 슬러그를 생성하는 전체 파이프라인을 담당한다.

파이프라인 구성:
  Stage 0 — 파일 캐시 조회 (LLM 호출 전 선행)
            · 이전 실행에서 정규화된 결과를 JSON 파일에서 불러온다.
            · 캐시 히트 시 LLM 호출 없이 즉시 반환한다.
  Stage 1 — LLM 시맨틱 정규화 (Claude API, temperature=0, 캐시 미스 시에만 실행)
            · 오타 보정
            · 약어 복원
            · 영어 → 한글 공식 명칭 변환
  Stage 2 — 결정론적 문자열 정규화 (순수 Python)
            · NFC 유니코드 정규화
            · 라틴 문자 소문자화
            · 공백·특수문자 제거
  Stage 3 — 접두사 부착
            · 자사 상품: own_
            · 경쟁 상품: comp_

캐시 키 설계:
  raw_name에 deterministic_normalize()를 적용한 값을 키로 사용한다.
  이렇게 하면 "토스 트래블카드"와 "토스트래블카드"처럼
  공백·대소문자만 다른 입력이 동일한 키로 수렴해 같은 캐시 항목을 공유한다.

  예:
    "토스 트래블카드"  → 키: "토스트래블카드"  → 캐시에 있으면 즉시 반환
    "토스트래블카드"   → 키: "토스트래블카드"  → 동일 키 → 히트
    "Toss 트래블카드"  → 키: "toss트래블카드"  → 별도 키 (처음엔 미스, 이후 히트)
    "토트카"          → 키: "토트카"          → 별도 키 (처음엔 미스, 이후 히트)

사용 위치:
  Express 라우트 핸들러 (POST /api/analysis/start)에서
  사용자가 폼을 승인한 직후, compiled_graph.invoke() 호출 전에 실행한다.

⚠️  LLM 호출은 반드시 Claude API(temperature=0)를 사용한다.
    ClaudeCodeCliAnalyzer는 temperature 제어가 불가하므로
    이 파이프라인에서 사용하지 않는다.
"""

import json
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import anthropic


# ── Stage 0: 파일 캐시 ───────────────────────────────────────────────────

class NormalizationCache:
    """
    상품명 정규화 결과를 JSON 파일에 영구 저장하는 캐시.

    캐시 파일 구조:
    {
      "_meta": {
        "created_at": "2026-04-27T...",
        "updated_at": "2026-04-27T...",
        "total_entries": 12
      },
      "entries": {
        "토스트래블카드":   "토스 트래블카드",
        "toss트래블카드":   "토스 트래블카드",
        "토트카":          "토스 트래블카드",
        "하나트래블로그카드": "하나 트래블로그 카드"
      }
    }

    캐시 키:
      deterministic_normalize(raw_name) 결과.
      공백·대소문자만 다른 입력은 동일한 키로 수렴한다.

    스레드 안전성:
      _lock을 사용해 파일 읽기/쓰기 중 동시 접근을 방지한다.

    Parameters
    ----------
    cache_path : str | Path
        캐시 파일 경로. 상위 디렉토리가 없으면 자동 생성한다.
        기본값: data/cache/product_name_normalization.json
    """

    def __init__(self, cache_path: str | Path = "data/cache/product_name_normalization.json"):
        self._path  = Path(cache_path)
        self._lock  = threading.Lock()
        self._cache = self._load()

    # ── 공개 API ──────────────────────────────────────────────────────────

    def get(self, key: str) -> str | None:
        """
        캐시에서 정규화된 공식 상품명을 조회한다.

        Parameters
        ----------
        key : str
            deterministic_normalize(raw_name) 결과 문자열.

        Returns
        -------
        str | None
            캐시 히트 시 공식 상품명, 미스 시 None.
        """
        with self._lock:
            return self._cache["entries"].get(key)

    def set(self, key: str, canonical_name: str) -> None:
        """
        정규화 결과를 캐시에 저장하고 파일에 즉시 반영한다.

        Parameters
        ----------
        key : str
            deterministic_normalize(raw_name) 결과 문자열.
        canonical_name : str
            LLM이 반환한 공식 상품명.
        """
        with self._lock:
            self._cache["entries"][key] = canonical_name
            self._cache["_meta"]["updated_at"]   = _now_iso()
            self._cache["_meta"]["total_entries"] = len(self._cache["entries"])
            self._save()

    def stats(self) -> dict:
        """캐시 현황(항목 수, 생성·수정 시각)을 반환한다."""
        with self._lock:
            return dict(self._cache["_meta"])

    # ── 내부 메서드 ───────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                # 구버전 캐시(entries 키 없이 flat dict 형태) 호환
                if "entries" not in data:
                    return self._empty_structure(existing_entries=data)
                return data
            except (json.JSONDecodeError, KeyError):
                return self._empty_structure()
        return self._empty_structure()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _empty_structure(existing_entries: dict | None = None) -> dict:
        now = _now_iso()
        return {
            "_meta": {
                "created_at":    now,
                "updated_at":    now,
                "total_entries": len(existing_entries) if existing_entries else 0,
            },
            "entries": existing_entries or {},
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


# ── Stage 1: LLM 시맨틱 정규화 ───────────────────────────────────────────

NORMALIZATION_SYSTEM_PROMPT = """\
당신은 한국 상품·브랜드명 정규화 전문가입니다.

입력으로 받은 상품명 또는 브랜드명을 아래 규칙에 따라 정규화해 반환하십시오.

[정규화 규칙]
1. 영어 → 한글 변환 (공식 명칭 우선)
   - 한국에서 공식 한글 명칭을 사용하는 브랜드는 반드시 공식 한글명으로 변환한다.
     예: toss → 토스, naver → 네이버, kakao → 카카오,
         kakao bank → 카카오뱅크, kakao pay → 카카오페이,
         hana card → 하나카드, shinhan → 신한, kb card → KB국민카드
   - 공식 명칭이 영문인 브랜드는 영문을 유지한다.
     예: Samsung Pay → Samsung Pay, LG ThinQ → LG ThinQ
   - 공식 혼용 표기는 그대로 유지한다.
     예: 카카오T → 카카오T, KB국민카드 → KB국민카드

2. 오타 보정
   - 가장 가능성 높은 공식 명칭으로 보정한다.
     예: 트레블카드 → 트래블카드, 네이버 파이 → 네이버페이

3. 약어 복원
   - 공식 전체 명칭으로 복원한다.
     예: 토트카 → 토스 트래블카드, 트래블로그 → 하나 트래블로그 카드

4. 일관성
   - 동일한 상품에 대해 항상 동일한 명칭을 반환한다.
   - 확신이 없으면 가장 가능성 높은 공식명을 반환한다.

[출력 형식]
정규화된 상품명 문자열만 반환한다. 설명, 따옴표, 줄바꿈 없이 텍스트만 출력한다.
"""


def llm_normalize(
    raw_name: str,
    client: anthropic.Anthropic,
    cache: NormalizationCache | None = None,
) -> str:
    """
    상품명을 시맨틱 정규화한다. 캐시가 있으면 LLM 호출을 건너뛴다.

    캐시 키는 deterministic_normalize(raw_name)으로 계산한다.
    같은 상품의 공백·대소문자 변형은 동일한 캐시 항목을 공유한다.

    Parameters
    ----------
    raw_name : str
        정규화할 원본 상품명.
    client : anthropic.Anthropic
        Anthropic API 클라이언트. 캐시 미스 시에만 사용한다.
    cache : NormalizationCache | None
        파일 캐시. None이면 캐시 없이 항상 LLM을 호출한다.

    Returns
    -------
    str
        정규화된 공식 상품명.
        예: "toss travel card" → "토스 트래블카드"

    Notes
    -----
    - LLM 호출 시 temperature=0을 강제해 결정론성을 보장한다.
    - 경량 모델(claude-haiku)을 사용해 비용을 최소화한다.
    """
    # 캐시 키: raw_name의 결정론적 정규화 결과
    cache_key = deterministic_normalize(raw_name)

    # Stage 0 — 캐시 조회
    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached  # ← LLM 호출 없이 즉시 반환

    # Stage 1 — LLM 호출 (캐시 미스 시)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        temperature=0,                    # 결정론성 보장 — 필수
        system=NORMALIZATION_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"정규화할 상품명: {raw_name}"}
        ],
    )
    canonical_name = response.content[0].text.strip()

    # 결과를 캐시에 저장
    if cache is not None:
        cache.set(cache_key, canonical_name)

    return canonical_name


# ── Stage 2: 결정론적 문자열 정규화 ──────────────────────────────────────

def deterministic_normalize(name: str) -> str:
    """
    상품명을 결정론적으로 슬러그 형태로 변환한다.

    처리 순서:
      1. NFC 유니코드 정규화 (같은 글자의 다른 인코딩 통일)
      2. 라틴 문자 소문자화 (한글은 대소문자 없으므로 그대로)
      3. 공백·하이픈·점·슬래시 등 구분자 제거
      4. 한글·영문·숫자 외 특수문자 제거

    Parameters
    ----------
    name : str
        정규화할 상품명. LLM 출력 또는 raw_name 모두 허용.

    Returns
    -------
    str
        슬러그용 정규화 문자열.

    Examples
    --------
    >>> deterministic_normalize("토스 트래블카드")
    '토스트래블카드'
    >>> deterministic_normalize("KB국민카드")
    'kb국민카드'
    >>> deterministic_normalize("카카오T")
    '카카오t'
    >>> deterministic_normalize("Samsung Pay")
    'samsungpay'
    >>> deterministic_normalize("Toss 트래블카드")
    'toss트래블카드'
    """
    name = unicodedata.normalize("NFC", name)
    name = "".join(c.lower() if c.isascii() and c.isalpha() else c for c in name)
    name = re.sub(r"[\s\-_·•./\\()\[\]]+", "", name)
    name = re.sub(r"[^가-힣a-z0-9]", "", name)
    return name


# ── Stage 3: 접두사 부착 ─────────────────────────────────────────────────

ProductRole = Literal["own", "comp"]


def attach_prefix(slug: str, role: ProductRole) -> str:
    """
    슬러그에 역할 접두사를 붙인다.

    Parameters
    ----------
    slug : str
        deterministic_normalize() 결과 슬러그.
    role : "own" | "comp"
        자사 상품이면 "own", 경쟁 상품이면 "comp".

    Returns
    -------
    str
        예: ("토스트래블카드", "own")      → "own_토스트래블카드"
             ("하나트래블로그카드", "comp") → "comp_하나트래블로그카드"
    """
    prefix = "own_" if role == "own" else "comp_"
    return f"{prefix}{slug}"


# ── 통합 파이프라인 ───────────────────────────────────────────────────────

class ProductIdResolver:
    """
    상품명 입력을 받아 product_id를 생성하는 전체 파이프라인.

    Stage 0(캐시) → Stage 1(LLM) → Stage 2(결정론적) → Stage 3(접두사)

    캐시 동작:
      - 처음 보는 raw_name → LLM 호출 → 결과를 파일 캐시에 저장
      - 이전에 처리한 raw_name → 캐시에서 즉시 반환 (LLM 호출 없음)
      - 서버 재시작 후에도 파일 캐시가 유지되므로 반복 비용이 발생하지 않는다.

    Parameters
    ----------
    api_key : str | None
        Anthropic API 키. None이면 환경변수 ANTHROPIC_API_KEY를 사용한다.
    cache_path : str | Path
        캐시 파일 경로. 기본값: data/cache/product_name_normalization.json

    Examples
    --------
    >>> resolver = ProductIdResolver()

    >>> # 자사 상품 (영어 입력) — 처음 실행: LLM 호출
    >>> resolver.resolve("toss travel card", role="own")
    ('토스 트래블카드', 'own_토스트래블카드')

    >>> # 동일 입력 재실행 — 캐시 히트: LLM 호출 없음
    >>> resolver.resolve("toss travel card", role="own")
    ('토스 트래블카드', 'own_토스트래블카드')

    >>> # 약어 입력
    >>> resolver.resolve("토트카", role="own")
    ('토스 트래블카드', 'own_토스트래블카드')

    >>> # 경쟁 상품
    >>> resolver.resolve("하나 트래블로그", role="comp")
    ('하나 트래블로그 카드', 'comp_하나트래블로그카드')
    """

    def __init__(
        self,
        api_key: str | None = None,
        cache_path: str | Path = "data/cache/product_name_normalization.json",
    ):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._cache  = NormalizationCache(cache_path)

    def resolve(self, raw_name: str, role: ProductRole) -> tuple[str, str]:
        """
        raw_name을 product_id로 변환한다.

        Parameters
        ----------
        raw_name : str
            사용자 입력 또는 QueryIntakeAgent가 반환한 own_product.name.
            오타, 약어, 영어 입력 모두 허용.
        role : "own" | "comp"
            자사 상품이면 "own", 경쟁 상품이면 "comp".

        Returns
        -------
        tuple[str, str]
            (canonical_name, product_id)
        """
        # Stage 0+1 — 캐시 조회 후 필요 시 LLM 정규화
        canonical_name = llm_normalize(raw_name, self._client, cache=self._cache)

        # Stage 2 — 결정론적 문자열 정규화
        slug = deterministic_normalize(canonical_name)

        # Stage 3 — 접두사 부착
        product_id = attach_prefix(slug, role)

        return canonical_name, product_id

    def resolve_own(self, raw_name: str) -> tuple[str, str]:
        """자사 상품용 단축 메서드."""
        return self.resolve(raw_name, role="own")

    def resolve_comp(self, raw_name: str) -> tuple[str, str]:
        """경쟁 상품용 단축 메서드."""
        return self.resolve(raw_name, role="comp")

    def cache_stats(self) -> dict:
        """캐시 현황을 반환한다."""
        return self._cache.stats()


# ── Express 라우트 핸들러 사용 예시 ──────────────────────────────────────
#
# from server.utils.slug import ProductIdResolver
#
# resolver = ProductIdResolver()   # 서버 기동 시 1회 생성, 캐시 파일 로드
#
# @app.post("/api/analysis/start")
# async def start_analysis(approved_form: dict):
#     own_product_name = approved_form["draft"]["own_product"]["name"]
#
#     # 캐시 히트 시 LLM 호출 없음
#     canonical_name, product_id = resolver.resolve_own(own_product_name)
#     project_id = "proj_" + product_id[4:]   # "own_" → "proj_"
#
#     initial_state = {
#         **approved_form["draft"],
#         "project_id": project_id,
#         "own_product": {
#             **approved_form["draft"]["own_product"],
#             "product_id": product_id,
#             "name":       canonical_name,
#         },
#     }
#     result = compiled_graph.invoke(initial_state)
#     return result
