"""
server/graph/nodes/feature_url_mapper_node.py
----------------------------------------------
FeatureUrlMapperAgent LangGraph 노드.

처리 흐름 (3단계)
-----------------
  Step 1 — Page Meta 수집 (HTTP)
      official_sources에서 validated URL을 추출하고
      ThreadPoolExecutor로 병렬 GET → <title> + <meta name="description"> 수집.
      수집 실패 시 공백 문자열로 처리하고 계속 진행.

  Step 2 — LLM 호출 (1회)
      도메인 컨텍스트 + domain_taxonomy + URL 메타데이터를 input.schema.json 형식으로 조립.
      taxonomy의 active_purposes·purpose_config를 포함해 LLM 1회 호출.
      LLM은 feature를 직접 생성하지 않고, taxonomy feature 목록을 수신해
      feature × candidate URL 커버리지 매핑과 additional_urls 제안에 집중한다.
      taxonomy url_types + url_type_priority가 additional_urls 제안 우선순위를 결정한다.

  Step 3 — Additional URL HTTP 검증
      LLM이 제안한 additional_urls를 ThreadPoolExecutor로 병렬 검증.
      각 URL에 validated, http_status 필드를 추가.

출력 state 키: analysis_features (list[AnalysisFeature])
  각 feature에 purpose_id 필드 포함 → feature_selection UI에서 purpose 단위 그룹핑에 사용.

taxonomy → feature_id 변환:
  taxonomy feature ID (접두사 없음): "transaction_fee_rate"
  출력 feature_id (feat_ 접두사):   "feat_transaction_fee_rate"
  변환은 _build_llm_input()에서 purpose_config를 가공할 때 명시적으로 안내되고
  LLM 프롬프트에도 규칙으로 기술된다. 노드는 LLM 출력의 feat_ 접두사를 그대로 신뢰한다.

전제조건:
  domain_modeling_node가 먼저 실행되어 state.domain_taxonomy가 존재해야 한다.

official_sources 항목 구조 (입력 참고):
  official 항목:
    {candidate_id, source_type="official", brand, product_name,
     primary_url, http_status, validated, fallback_urls, llm_confidence}

  reference 항목:
    {candidate_id, source_type="reference", method_name, provider_type,
     reference_sources: [{url, validated, http_status, final_url, ...}],
     note, validated, primary_url=None}
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import re

import requests as req_lib

from server.config import AGENTS_DIR, CLI_MODEL, CLI_TIMEOUT, FEATURE_URL_MAPPER_PARALLEL
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.state import AnalysisFeature, DomainAnalysisState, AgentStep
from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer

logger = logging.getLogger(__name__)

_HTTP_CONNECT_TIMEOUT = 3
_HTTP_READ_TIMEOUT    = 7
_HTTP_TIMEOUT         = (_HTTP_CONNECT_TIMEOUT, _HTTP_READ_TIMEOUT)
_USER_AGENT = (
    "Mozilla/5.0 (compatible; FeatureUrlMapperBot/1.0)"
)
_MAX_WORKERS     = 10   # 병렬 HTTP 스레드 수
_META_BODY_LIMIT = 8_000  # HTML 본문 파싱 최대 바이트 (메모리 절약)


# ─────────────────────────────────────────────────── 공개 노드 함수 ──────────

def feature_url_mapper_node(state: DomainAnalysisState) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()

    # ── 에이전트 파일 로드 ───────────────────────────────────────────────────
    agent_dir     = AGENTS_DIR / "feature_url_mapper"
    system_prompt = _load_text(agent_dir / "system_prompt_kr.md")
    output_schema = _load_json(agent_dir / "output.schema.json")

    if system_prompt is None:
        return _error(started_at, f"시스템 프롬프트 없음: {agent_dir}")
    if output_schema is None:
        return _error(started_at, f"출력 스키마 없음: {agent_dir}")

    # ── 입력 수집 ────────────────────────────────────────────────────────────
    official_sources: list[dict] = state.get("official_sources") or []
    domain_name: str             = state.get("domain_name") or ""
    own_product: dict            = state.get("own_product") or {}
    domain_taxonomy: dict        = state.get("domain_taxonomy") or {}

    if not official_sources:
        return _error(started_at, "official_sources가 state에 없습니다.")
    if not domain_name:
        return _error(started_at, "domain_name이 state에 없습니다.")
    if not domain_taxonomy:
        return _error(
            started_at,
            "domain_taxonomy가 state에 없습니다. "
            "domain_modeling_node가 먼저 실행되어야 합니다.",
        )
    if not domain_taxonomy.get("active_purposes"):
        return _error(
            started_at,
            "domain_taxonomy.active_purposes가 비어 있습니다. "
            "taxonomy 생성 결과를 확인하세요.",
        )

    # ── Step 1: 검증된 URL별 page meta 수집 ──────────────────────────────────
    logger.info("feature_url_mapper_node: Step 1 — page meta 수집 시작")
    meta_by_url: dict[str, dict] = _collect_page_meta(official_sources)
    logger.info("feature_url_mapper_node: Step 1 완료 (%d개 URL 처리)", len(meta_by_url))

    # ── Step 2: LLM 입력 조립 + 호출 ─────────────────────────────────────────
    llm_input = _build_llm_input(
        domain_name=domain_name,
        own_product=own_product,
        official_sources=official_sources,
        meta_by_url=meta_by_url,
        domain_taxonomy=domain_taxonomy,
    )

    active_purposes = domain_taxonomy.get("active_purposes", [])
    purpose_config  = domain_taxonomy.get("purpose_config", {})
    feature_count   = sum(
        len(purpose_config.get(p, {}).get("features", []))
        for p in active_purposes
    )
    logger.info(
        "feature_url_mapper_node: Step 2 — LLM/cache 준비 "
        "(purposes=%d, features=%d, candidates=%d, parallel=%d)",
        len(active_purposes), feature_count,
        len(llm_input["candidates"]), FEATURE_URL_MAPPER_PARALLEL,
    )

    cache_context = make_cache_context(
        agent_id="feature_url_mapper",
        model=CLI_MODEL,
        system_prompt=system_prompt,
        output_schema=output_schema,
        prompt_version="feature_url_mapper:v1",
    )
    cache_input = llm_input
    llm_output = load_agent_output(
        agent_id="feature_url_mapper",
        cache_input=cache_input,
        context=cache_context,
        output_schema=output_schema,
        logger=logger,
    )

    if llm_output is None:
        # ── purpose별 병렬 LLM 호출 ──────────────────────────────────────────
        # 설계 배경:
        #   단일 호출 방식은 8 purposes × 7 features × N candidates = 156+ coverage
        #   항목을 한 번에 생성하므로 300초도 초과할 수 있다.
        #   purpose별로 분리하면 각 호출이 ~21 coverage 항목만 생성 (입력 ~1KB).
        #   ThreadPoolExecutor로 FEATURE_URL_MAPPER_PARALLEL개씩 병렬 실행한다.
        #   (기본 2: 4라운드 × ~45s ≈ 3분 / 3으로 올리면 ≈ 2분)
        # 캐시:
        #   cache_input은 전체 llm_input 기준 — purpose별 분리는 LLM 호출 최적화일 뿐.
        #   캐시 히트 시 병렬 호출 없이 즉시 반환된다.
        analyzer      = ClaudeCodeCliAnalyzer(
            model=CLI_MODEL, timeout=CLI_TIMEOUT, system_prompt=system_prompt
        )
        relaxed_schema = _strip_schema_patterns(output_schema)
        candidates     = llm_input["candidates"]
        own_product_slim = llm_input["own_product"]

        def _call_for_purpose(purpose_id: str) -> list[dict]:
            """단일 purpose에 대한 LLM 호출 (ThreadPoolExecutor 대상)."""
            p_input = {
                "domain":         domain_name,
                "own_product":    own_product_slim,
                "purpose_id":     purpose_id,
                "purpose_config": {purpose_id: purpose_config.get(purpose_id, {})},
                "candidates":     candidates,
            }
            p_prompt = (
                "아래 입력 데이터를 분석하여 output schema를 만족하는 JSON만 반환하라.\n\n"
                "규칙:\n"
                "1. purpose_config의 해당 purpose_id features만 처리한다. 임의로 추가·삭제하지 않는다.\n"
                "2. 각 feature_id는 taxonomy feature ID 앞에 feat_ 접두사를 붙인다.\n"
                "   예) 'transaction_fee_rate' → 'feat_transaction_fee_rate'\n"
                "3. purpose_id는 입력의 purpose_id 값을 그대로 사용한다.\n"
                "4. additional_urls 제안 시 url_type_priority 오름차순으로 url_types를 참고한다.\n"
                "5. coverage='sufficient'이면 additional_urls는 반드시 빈 배열 []을 반환한다.\n"
                "6. 출력 features 순서는 purpose_config의 features 순서를 따른다.\n\n"
                f"입력:\n{json.dumps(p_input, ensure_ascii=False, separators=(',', ':'))}"
            )
            result = analyzer.call_with_schema(
                prompt=p_prompt, output_schema=relaxed_schema
            )
            return result.get("features", [])

        results_by_purpose: dict[str, list[dict]] = {}
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=FEATURE_URL_MAPPER_PARALLEL) as pool:
            future_map = {
                pool.submit(_call_for_purpose, p): p
                for p in active_purposes
            }
            for future in as_completed(future_map):
                purpose_id = future_map[future]
                try:
                    feats = future.result()
                    results_by_purpose[purpose_id] = feats
                    logger.info(
                        "feature_url_mapper_node: purpose=%s 완료 (features=%d)",
                        purpose_id, len(feats),
                    )
                except RuntimeError as exc:
                    logger.error(
                        "feature_url_mapper_node: purpose=%s LLM 실패 — %s",
                        purpose_id, exc,
                    )
                    errors.append(f"{purpose_id}: {str(exc)[:120]}")

        if errors:
            return _error(started_at, f"일부 purpose LLM 호출 실패:\n" + "\n".join(errors))

        # active_purposes 순서 보장 (as_completed는 완료 순 반환)
        all_features = []
        for p in active_purposes:
            all_features.extend(results_by_purpose.get(p, []))

        llm_output = {"features": all_features}
        store_agent_output(
            agent_id="feature_url_mapper",
            cache_input=cache_input,
            context=cache_context,
            output=llm_output,
            logger=logger,
        )

    raw_features: list[dict] = llm_output.get("features", [])
    logger.info(
        "feature_url_mapper_node: Step 2 완료 (features=%d)", len(raw_features)
    )

    # ── Step 3: additional_urls 병렬 HTTP 검증 ───────────────────────────────
    logger.info("feature_url_mapper_node: Step 3 — additional_urls 검증 시작")
    analysis_features = _validate_additional_urls(raw_features)
    logger.info("feature_url_mapper_node: Step 3 완료")

    finished_at = datetime.now(timezone.utc).isoformat()

    step: AgentStep = {
        "step_name":   "FeatureUrlMapper",
        "status":      "completed",
        "started_at":  started_at,
        "finished_at": finished_at,
    }
    return {"analysis_features": analysis_features, "agent_steps": [step]}


# ─────────────────────────────── Step 1: page meta 수집 ──────────────────────

def _collect_page_meta(official_sources: list[dict]) -> dict[str, dict]:
    """
    official_sources에서 validated URL을 모두 추출하고
    ThreadPoolExecutor로 병렬 HTTP GET → page_title, meta_description 수집.

    Returns
    -------
    dict[str, dict]
        {url: {"page_title": str, "meta_description": str}}
    """
    # 수집 대상 URL 목록 (중복 제거)
    urls_to_fetch: set[str] = set()

    for src in official_sources:
        stype = src.get("source_type")
        if stype == "official":
            if src.get("validated") and src.get("primary_url"):
                urls_to_fetch.add(src["primary_url"])
        elif stype == "reference":
            for ref in src.get("reference_sources", []):
                if ref.get("validated") and ref.get("final_url"):
                    urls_to_fetch.add(ref["final_url"])
                elif ref.get("validated") and ref.get("url"):
                    urls_to_fetch.add(ref["url"])

    if not urls_to_fetch:
        logger.warning("feature_url_mapper_node: 수집할 validated URL이 없습니다.")
        return {}

    meta_by_url: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        future_map = {pool.submit(_fetch_meta, url): url for url in urls_to_fetch}
        for future in as_completed(future_map):
            url = future_map[future]
            try:
                meta = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("page meta 수집 예외 (%s): %s", url, exc)
                meta = {"page_title": "", "meta_description": ""}
            meta_by_url[url] = meta

    return meta_by_url


def _fetch_meta(url: str) -> dict:
    """
    단일 URL에 GET 요청을 보내 <title>과 <meta name="description"> content를 반환한다.
    실패 시 빈 문자열을 반환한다.
    """
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html"}
    try:
        resp = req_lib.get(
            url,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        if resp.status_code < 200 or resp.status_code >= 400:
            return {"page_title": "", "meta_description": ""}

        # Content-Type 확인: HTML이 아니면 파싱 불필요
        ctype = resp.headers.get("Content-Type", "").lower()
        if "html" not in ctype:
            return {"page_title": "", "meta_description": ""}

        # 메모리 절약을 위해 앞부분만 읽기
        raw_bytes = b""
        for chunk in resp.iter_content(chunk_size=2048):
            raw_bytes += chunk
            if len(raw_bytes) >= _META_BODY_LIMIT:
                break

        html_text = raw_bytes.decode("utf-8", errors="replace")
        parser = _MetaExtractor()
        parser.feed(html_text)
        return {
            "page_title":       (parser.title or "").strip(),
            "meta_description": (parser.meta_desc or "").strip(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("_fetch_meta 예외 (%s): %s", url, exc)
        return {"page_title": "", "meta_description": ""}


class _MetaExtractor(HTMLParser):
    """HTML 앞부분에서 <title>과 <meta name="description"> / <meta property="og:description">를 추출한다."""

    def __init__(self):
        super().__init__()
        self.title: str | None      = None
        self.meta_desc: str | None  = None
        self._in_title: bool        = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            attrs_dict = {k.lower(): (v or "") for k, v in attrs}
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            if (name == "description" or prop == "og:description") and self.meta_desc is None:
                self.meta_desc = attrs_dict.get("content", "")

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            stripped = data.strip()
            if stripped:
                self.title = stripped

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False


# ─────────────────────────────── Step 2: LLM 입력 조립 ──────────────────────

def _build_llm_input(
    domain_name: str,
    own_product: dict,
    official_sources: list[dict],
    meta_by_url: dict[str, dict],
    domain_taxonomy: dict,
) -> dict:
    """
    official_sources·page meta·domain_taxonomy를 조합해 input.schema.json 형식의 LLM 입력을 만든다.

    taxonomy 처리
    -------------
    - active_purposes, purpose_config를 그대로 포함한다.
    - LLM이 feature_id 생성 시 feat_ 접두사를 붙이도록 user_prompt에서 안내되므로
      이 함수에서는 taxonomy 원본 값을 변환 없이 전달한다.

    candidates 처리
    ---------------
    - validated=True인 항목만 validated_urls에 포함한다.
    - validated URL이 없는 항목은 빈 validated_urls로 포함해
      LLM이 not_found coverage를 부여할 수 있도록 한다.
    """
    candidates: list[dict] = []

    for src in official_sources:
        cid   = src.get("candidate_id", "")
        stype = src.get("source_type")

        if stype == "official":
            validated_urls = []
            if src.get("validated") and src.get("primary_url"):
                url  = src["primary_url"]
                meta = meta_by_url.get(url, {})
                validated_urls.append({
                    "url":              url,
                    "page_title":       meta.get("page_title", ""),
                    "meta_description": meta.get("meta_description", ""),
                })
            candidates.append({
                "candidate_id":   cid,
                "source_type":    "official",
                "validated_urls": validated_urls,
            })

        elif stype == "reference":
            validated_urls = []
            for ref in src.get("reference_sources", []):
                if ref.get("validated"):
                    url  = ref.get("final_url") or ref.get("url", "")
                    meta = meta_by_url.get(url, {})
                    validated_urls.append({
                        "url":              url,
                        "page_title":       meta.get("page_title", ""),
                        "meta_description": meta.get("meta_description", ""),
                    })
            candidates.append({
                "candidate_id":   cid,
                "source_type":    "reference",
                "validated_urls": validated_urls,
            })

    return {
        "domain":           domain_name,
        "own_product": {
            "brand":        own_product.get("brand", ""),
            "product_name": own_product.get("name", own_product.get("product_name", "")),
        },
        "active_purposes":  domain_taxonomy.get("active_purposes", []),
        "purpose_config":   domain_taxonomy.get("purpose_config", {}),
        "candidates":       candidates,
    }


# ─────────────────────────── Step 3: additional_urls 검증 ────────────────────

def _validate_additional_urls(raw_features: list[dict]) -> list[AnalysisFeature]:
    """
    LLM 출력의 additional_urls를 병렬 HTTP 검증하고
    각 URL에 validated, http_status 필드를 추가한다.
    coverage가 sufficient인 항목은 additional_urls = [] 이므로 검증 대상 없음.
    """
    # 검증 대상 수집: (feat_idx, cov_idx, url_idx, url)
    tasks: list[tuple[int, int, int, str]] = []

    for fi, feat in enumerate(raw_features):
        for ci, cov in enumerate(feat.get("candidate_coverage", [])):
            for ui, au in enumerate(cov.get("additional_urls", [])):
                url = au.get("url", "").strip()
                if url:
                    tasks.append((fi, ci, ui, url))

    if not tasks:
        # 검증 대상 없음 — 그대로 반환 (validated 필드 미설정)
        return [_normalize_feature(f) for f in raw_features]

    # 병렬 검증
    val_results: dict[tuple[int, int, int], tuple[int | None]] = {}

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        future_map = {
            pool.submit(_check_url_status, url): (fi, ci, ui)
            for fi, ci, ui, url in tasks
        }
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                status = future.result()
            except Exception:  # noqa: BLE001
                status = None
            val_results[key] = status

    logger.info(
        "feature_url_mapper_node: additional_urls 검증 완료 (%d개)", len(tasks)
    )

    # 결과 반영
    for fi, ci, ui, _ in tasks:
        status = val_results.get((fi, ci, ui))
        au = raw_features[fi]["candidate_coverage"][ci]["additional_urls"][ui]
        au["validated"]   = bool(status and 200 <= status < 400)
        au["http_status"] = status

    return [_normalize_feature(f) for f in raw_features]


def _check_url_status(url: str) -> int | None:
    """HEAD → GET 순으로 HTTP 상태 코드만 반환한다."""
    headers = {"User-Agent": _USER_AGENT}
    for method in ("HEAD", "GET"):
        try:
            resp = req_lib.request(
                method, url,
                headers=headers,
                timeout=_HTTP_TIMEOUT,
                allow_redirects=True,
                stream=(method == "GET"),
            )
            if method == "HEAD" and resp.status_code == 405:
                continue
            return resp.status_code
        except req_lib.exceptions.SSLError:
            continue
        except (req_lib.exceptions.ConnectionError,
                req_lib.exceptions.Timeout):
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("_check_url_status 예외 (%s): %s", url, exc)
            return None
    return None


def _normalize_feature(raw: dict) -> AnalysisFeature:
    """
    LLM 출력 dict를 AnalysisFeature TypedDict 형태로 정규화한다.

    - purpose_id: 대문자 혼용값이 있으면 lowercase snake_case로 변환
    - additional_urls: validated/http_status 기본값 채우기
    """
    # purpose_id 정규화 (taxonomy ID와 일치시키기 위해)
    if "purpose_id" in raw and isinstance(raw["purpose_id"], str):
        raw["purpose_id"] = re.sub(r"[^a-zA-Z0-9]+", "_", raw["purpose_id"]).lower().strip("_") or "unknown"

    for cov in raw.get("candidate_coverage", []):
        for au in cov.get("additional_urls", []):
            au.setdefault("validated", False)
            au.setdefault("http_status", None)
    return raw  # type: ignore[return-value]


def _strip_schema_patterns(schema: object) -> object:
    """
    JSON Schema에서 모든 'pattern' 제약을 재귀적으로 제거한다.

    LLM 호출 시 패턴 검증 실패로 인한 retry 루프를 방지하기 위해
    relaxed schema를 사용한다. 출력은 _normalize_feature()로 보정한다.
    """
    if isinstance(schema, dict):
        return {k: _strip_schema_patterns(v) for k, v in schema.items() if k != "pattern"}
    if isinstance(schema, list):
        return [_strip_schema_patterns(item) for item in schema]
    return schema


# ─────────────────────────────────────────────────────── 내부 헬퍼 ───────────

def _load_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("파일 없음: %s", path)
        return None


def _load_json(path: Path) -> dict | None:
    text = _load_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("JSON 파싱 실패 (%s): %s", path, exc)
        return None


def _error(started_at: str, message: str) -> dict:
    logger.error("feature_url_mapper_node 오류: %s", message)
    return {
        "errors": [{"node": "feature_url_mapper_node",
                    "error": message,
                    "timestamp": datetime.now(timezone.utc).isoformat()}],
        "agent_steps": [{"step_name": "FeatureUrlMapper",
                         "status": "failed",
                         "started_at": started_at,
                         "finished_at": datetime.now(timezone.utc).isoformat(),
                         "error_message": message}],
    }
