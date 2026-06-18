"""
server/graph/nodes/reaction_analysis_node.py (v0.13 — reaction_insight 시리즈)
-------------------------------------------------------------------------------
사용자 반응 ABSA 노드 — 채널 2종 원자료를 aspect_codebook 기반 7-tuple 로 분해.
설계: docs/design/reaction_insight_node_design.md §5 (RI-D5 어댑터 = CLI)

책임 분리 (CM-D1 사상)
----------------------
- 코드: 채널 2종 입력 조립(dedup·candidate 연관) · 스레드 원자 chunk 분할(CH-D3·D4) ·
  LLM 출력 가드(코드북 외 aspect · 입력에 없는 source_url · 비실존 quote 제거) · 표본 메타
  (AP-3) 집계.
- LLM (ClaudeCodeCliAnalyzer): chunk별 7-tuple 추출. candidate×chunk 를 평탄화해 상한 풀로
  병렬 호출(CH-D11). 캐시 I/O 는 메인 스레드 전용(경쟁 회피).
  설계: docs/design/reaction_analysis_chunking_design.md (CONFIRMED)

read keys
---------
- selected_comments / collected_videos  (youtube_reaction_collection 산출)
- community_posts                       (community_collection 산출 → channel="community")
- blog_posts                            (blog_collection 산출 → channel="blog"; 미배선 시 빈 리스트)
- domain_taxonomy.report_config["reaction_insight"].aspect_codebook
- selected_purposes (게이트)

write keys
----------
- reaction_analysis : {candidate_id: {"tuples": [7-tuple+is_suggestion],
                       "channel_counts": {"youtube": n, "community": n, "blog": n},
                       "post_count": {...}, "sample_size": int (수집 전량),
                       "analyzed_size": int (분석 성공분), "collected_at": ISO8601,
                       "dropped_by_guard": int, "failed_chunks": int,
                       "missing_items": int}}
- agent_steps / errors (누적 reducer)
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from server.config import (
    AGENTS_DIR,
    REACTION_ABSA_CHUNK_CHARS,
    REACTION_ABSA_CHUNK_CHARS_MIN,
    REACTION_ABSA_CHUNK_TIMEOUT,
    REACTION_ABSA_MAX_ITEMS,
    REACTION_ABSA_PARALLEL,
)
from server.graph.agent_cache import (
    load_agent_output,
    make_cache_context,
    store_agent_output,
)
from server.graph.progress_store import set_progress
from server.graph.state import AgentStep, DomainAnalysisState
from server.graph.nodes.feature_url_mapper_node import _load_json, _load_text

logger = logging.getLogger(__name__)

REPORT_TYPE   = "reaction_insight"
_LLM_AGENT_ID = "reaction_analysis"

_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """quote 실존 검증용 정규화 — 공백 제거 (LLM 의 공백 변형 허용)."""
    return _WS_RE.sub("", text or "")


def _group_sort_threads(items: list[dict]) -> list[list[dict]]:
    """thread_id 로 그룹화 + 결정론 정렬 (CH-D3). 반환: 스레드(item 리스트)들의 리스트.

    스레드 내부는 posted_at·source_url 오름차순(최상위/최초 먼저). 스레드 간은 대표(최초)
    posted_at 내림차순(최신 스레드 우선), 동률 시 thread_id 로 결정론적 순서를 보장한다.
    """
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it.get("thread_id", ""), []).append(it)
    threads: list[tuple[str, str, list[dict]]] = []
    for tid, group in groups.items():
        group.sort(key=lambda x: (x.get("posted_at", ""), x.get("source_url", "")))
        threads.append((group[0].get("posted_at", ""), tid, group))
    threads.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [g for _, _, g in threads]


def _thread_chars(thread: list[dict]) -> int:
    return sum(len(it.get("text", "")) for it in thread)


def _split_threads(threads: list[list[dict]], budget: int,
                   min_chars: int) -> list[list[dict]]:
    """스레드 원자 chunk 분할 (CH-D3·CH-D4). 스레드는 절대 분할하지 않는다.

    누적 text 길이 budget 까지 스레드를 담되, 다음 스레드가 budget 을 넘기면 현재 chunk 를
    닫는다. 단 현재 chunk 가 min_chars 미만이면(마지막 잔여 제외) 닫지 않고 계속 채워 비최종
    chunk 의 하한을 보장한다. 단일 스레드가 budget 초과 시 그 스레드만으로 1 chunk.
    """
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_chars = 0
    for thread in threads:
        tchars = _thread_chars(thread)
        if cur and cur_chars + tchars > budget and cur_chars >= min_chars:
            chunks.append(cur)
            cur, cur_chars = [], 0
        cur.extend(thread)
        cur_chars += tchars
    if cur:
        chunks.append(cur)
    return chunks


def _dedup_tuples(tuples: list[dict]) -> list[dict]:
    """경계 중복 환각 방어 — (aspect, source_url, norm(quote)) 기준 dedup (CH-D7)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for t in tuples:
        k = (t.get("aspect"), t.get("source_url"), _norm(t.get("quote", "")))
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


_CHANNELS = ("youtube", "community", "blog")


def _count_by_channel(items: list[dict]) -> dict[str, int]:
    return {ch: sum(1 for it in items if it.get("channel") == ch) for ch in _CHANNELS}


def _unique_urls_by_channel(items: list[dict]) -> dict[str, int]:
    return {
        ch: len({it["source_url"] for it in items
                 if it.get("channel") == ch and it.get("source_url")})
        for ch in _CHANNELS
    }


# ─── 코드 파트: 입력 조립 (순수 함수) ────────────────────────────────────────

def build_absa_inputs(state: dict) -> dict[str, list[dict]]:
    """candidate_id → ABSA 입력 items (채널 2종 통합·dedup).

    youtube: 댓글 1건 = item 1건 (source_url = 영상 watch URL).
    community: 게시글 1건 = item 1건 (제목 + 본문 발췌).
    각 item 은 `thread_id` 를 포함한다(YR-D4) — CH-D3 스레드 원자 chunk 경계가 소비하며,
    youtube 대댓글은 부모와 동일 thread_id, 그 외는 항목별 고유값.
    """
    if REPORT_TYPE not in (state.get("selected_purposes") or []):
        return {}

    video_url = {v.get("video_id", ""): v.get("url", "")
                 for v in state.get("collected_videos") or []}

    items: dict[str, list[dict]] = {}
    seen: dict[str, set[str]] = {}

    for c in state.get("selected_comments") or []:
        cid = c.get("candidate_id", "")
        text = (c.get("text") or "").strip()
        if not cid or not text:
            continue
        key = _norm(text)[:200]
        if key in seen.setdefault(cid, set()):
            continue
        seen[cid].add(key)
        items.setdefault(cid, []).append({
            "channel":    "youtube",
            "source_url": video_url.get(c.get("video_id", ""), "") or
                          f"https://www.youtube.com/watch?v={c.get('video_id', '')}",
            "posted_at":  c.get("published_at", ""),
            "text":       text,
            # YR-D4 — CH-D3 스레드 원자 chunk 경계가 소비. 대댓글은 부모와 동일 thread_id.
            # thread_id 미배선(구 데이터) 시 댓글별 고유값으로 폴백(각 댓글=1 스레드).
            "thread_id":  c.get("thread_id") or f"yt:{key}",
        })

    # community_posts → channel="community", blog_posts → channel="blog".
    # blog_posts 는 blog_collection_node(미배선/휴면) 산출이라 현재는 빈 리스트.
    for state_key, channel in (("community_posts", "community"),
                               ("blog_posts", "blog")):
        for p in state.get(state_key) or []:
            cid = p.get("candidate_id", "")
            body = (p.get("body_excerpt") or "").strip()
            if not cid or not body:
                continue
            text = f"{p.get('title', '')}\n{body}".strip()
            key = _norm(text)[:200]
            if key in seen.setdefault(cid, set()):
                continue
            seen[cid].add(key)
            items.setdefault(cid, []).append({
                "channel":    channel,
                "source_url": p.get("url", ""),
                "posted_at":  p.get("published_at", ""),
                "text":       text,
                # YR-D4 — 비유튜브는 스레드 개념 없음: 게시글 1건=원자 단위(고유 thread_id).
                "thread_id":  p.get("url") or f"{channel}:{key}",
            })

    return items


def _aspect_ids(state: dict) -> list[dict]:
    """taxonomy aspect_codebook → LLM 입력용 [{aspect_id, label, definition}]."""
    entry = ((state.get("domain_taxonomy") or {}).get("report_config") or {}) \
        .get(REPORT_TYPE) or {}
    aspects = []
    for a in entry.get("aspect_codebook") or []:
        if isinstance(a, dict) and a.get("aspect_id"):
            aspects.append({
                "aspect_id":  a["aspect_id"],
                "label":      a.get("label", a["aspect_id"]),
                "definition": a.get("definition", ""),
            })
        elif isinstance(a, str):
            aspects.append({"aspect_id": a, "label": a, "definition": ""})
    return aspects


# ─── 코드 파트: LLM 출력 가드 ────────────────────────────────────────────────

def sanitize_tuples(tuples: list[dict], items: list[dict],
                    valid_aspects: set[str]) -> tuple[list[dict], int]:
    """환각 제거 — 코드북 외 aspect · 입력에 없는 source_url · 비실존 quote.

    반환: (정제 tuple 목록, 제거 건수)
    """
    valid_urls = {it["source_url"] for it in items}
    corpus = _norm(" ".join(it["text"] for it in items))
    kept: list[dict] = []
    dropped = 0
    for t in tuples:
        if t.get("aspect") not in valid_aspects \
                or t.get("source_url") not in valid_urls \
                or not t.get("quote") \
                or _norm(t["quote"]) not in corpus:
            dropped += 1
            continue
        kept.append(t)
    return kept, dropped


# ─── 메인 노드 ───────────────────────────────────────────────────────────────

def reaction_analysis_node(
    state: DomainAnalysisState, config: dict | None = None, analyzer=None
) -> dict:
    """채널 2종 반응 원자료 → ABSA 7-tuple (candidate당 LLM 1회 + 가드)."""
    started_at = datetime.now(timezone.utc).isoformat()
    thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
    if thread_id:
        try:
            set_progress(thread_id, "reaction_analysis",
                         detail="사용자 반응 ABSA 분석")
        except Exception as exc:  # noqa: BLE001
            logger.debug("set_progress 실패: %s", exc)

    inputs = build_absa_inputs(dict(state))
    if not inputs:
        return {"agent_steps": [_step("skipped", started_at)]}

    aspects = _aspect_ids(dict(state))
    if not aspects:
        return _error_out(started_at,
                          "aspect_codebook 이 비어 있습니다 — domain_taxonomy 확인 필요.")
    valid_aspects = {a["aspect_id"] for a in aspects}

    system_prompt = _load_text(AGENTS_DIR / _LLM_AGENT_ID / "system_prompt_kr.md")
    output_schema = _load_json(AGENTS_DIR / _LLM_AGENT_ID / "output.schema.json")
    if system_prompt is None or output_schema is None:
        return _error_out(started_at, f"agents/{_LLM_AGENT_ID}/ prompt·schema 로드 실패.")

    if analyzer is None:
        from server.llm.claude_cli_analyzer import ClaudeCodeCliAnalyzer  # 지연 import
        analyzer = ClaudeCodeCliAnalyzer(system_prompt=system_prompt,
                                         timeout=REACTION_ABSA_CHUNK_TIMEOUT)  # CH-D6
    context = make_cache_context(
        agent_id=_LLM_AGENT_ID,
        model=getattr(analyzer, "model", "claude_cli"),
        system_prompt=system_prompt,
        output_schema=output_schema,
    )

    errors: list[dict] = []
    reaction_analysis: dict[str, dict] = {}
    name_by_cid = {
        p.get("candidate_id", ""): p.get("product_name", "")
        for p in state.get("product_profiles") or []
    }

    # ── 1) candidate별 스레드 정렬·chunk 분할 + 평탄화 작업목록 (CH-D3·D4) ─────
    chunks_by_cid: dict[str, tuple[list[dict], list[list[dict]]]] = {}
    tasks: list[tuple[str, int, list[dict]]] = []
    for cid in sorted(inputs):
        threads = _group_sort_threads(inputs[cid])
        # 안전 상한 (CH-D2) — 정상 운영 미도달. 스레드 단위로 누적해 초과분 폐기.
        kept_threads: list[list[dict]] = []
        cnt = 0
        for th in threads:
            if kept_threads and cnt + len(th) > REACTION_ABSA_MAX_ITEMS:
                logger.warning("reaction_analysis[%s]: MAX_ITEMS=%d 초과 — 이후 스레드 폐기",
                               cid, REACTION_ABSA_MAX_ITEMS)
                break
            kept_threads.append(th)
            cnt += len(th)
        chunks = _split_threads(kept_threads, REACTION_ABSA_CHUNK_CHARS,
                                REACTION_ABSA_CHUNK_CHARS_MIN)
        items_sorted = [it for ch in chunks for it in ch]
        chunks_by_cid[cid] = (items_sorted, chunks)
        for i, chunk in enumerate(chunks):
            tasks.append((cid, i, chunk))

    # ── 2) 캐시 조회 (메인 스레드 순차) → 적중/미스 분리 (CH-D5·D11) ───────────
    results: dict[tuple[str, int], list[dict]] = {}
    cache_inputs: dict[tuple[str, int], dict] = {}
    misses: list[tuple[str, int, list[dict]]] = []
    for cid, i, chunk in tasks:
        ci = {
            "candidate_id": cid,
            "aspect_ids":   sorted(valid_aspects),
            "items_sha":    [_norm(it["text"])[:64] for it in chunk],
        }
        cache_inputs[(cid, i)] = ci
        cached = load_agent_output(
            agent_id=_LLM_AGENT_ID, cache_input=ci,
            context=context, output_schema=output_schema, logger=logger)
        if cached is not None:
            results[(cid, i)] = cached.get("tuples", [])
        else:
            misses.append((cid, i, chunk))

    # ── 3) 미스만 평탄화 병렬 CLI 호출 (워커: call_with_schema 만) (CH-D11) ────
    # 워커는 LLM 호출만 수행한다. 캐시 I/O·종합은 메인 스레드 전용 — agent_cache 의
    # 비원자적 read-modify-write 경쟁(엔트리 유실) 회피.
    def _call_chunk(cid: str, chunk: list[dict]) -> dict:
        payload = {
            "candidate_id":   cid,
            "candidate_name": name_by_cid.get(cid, cid),
            "aspects":        aspects,
            "items":          chunk,
        }
        prompt = ("다음 사용자 반응에서 aspect 별 의견을 추출하라.\n\n```json\n"
                  + json.dumps(payload, ensure_ascii=False) + "\n```")
        return analyzer.call_with_schema(prompt, output_schema)

    if misses:
        with ThreadPoolExecutor(
            max_workers=min(len(misses), REACTION_ABSA_PARALLEL)
        ) as pool:
            fut_map = {pool.submit(_call_chunk, cid, chunk): (cid, i)
                       for cid, i, chunk in misses}
            for fut in as_completed(fut_map):
                cid, i = fut_map[fut]
                try:
                    out_i = fut.result()              # CH-D6 per-chunk timeout
                    results[(cid, i)] = out_i.get("tuples", [])
                    store_agent_output(               # 메인 스레드 — 경쟁 회피
                        agent_id=_LLM_AGENT_ID, cache_input=cache_inputs[(cid, i)],
                        context=context, output=out_i, logger=logger)
                except Exception as exc:  # noqa: BLE001 — chunk 단위 부분 실패 (CH-D10)
                    errors.append({
                        "node": "reaction_analysis_node",
                        "error": f"candidate={cid}/chunk={i}: "
                                 f"{type(exc).__name__}: {str(exc)[:200]}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

    # ── 4) candidate별 종합 (메인 스레드) (CH-D7·D8·D9·D10) ───────────────────
    for cid, (items_sorted, chunks) in chunks_by_cid.items():
        n = len(chunks)
        succeeded = [i for i in range(n) if (cid, i) in results]
        if not succeeded:                     # 전 chunk 실패 → candidate 누락 (CH-D10)
            continue
        merged = _dedup_tuples([t for i in succeeded for t in results[(cid, i)]])
        tuples, dropped = sanitize_tuples(merged, items_sorted, valid_aspects)
        analyzed = sum(len(chunks[i]) for i in succeeded)
        reaction_analysis[cid] = {
            "tuples":           tuples,
            "channel_counts":   _count_by_channel(items_sorted),
            "post_count":       _unique_urls_by_channel(items_sorted),
            "sample_size":      len(items_sorted),    # AP-3 — 수집 전량
            "analyzed_size":    analyzed,             # CH-D9 — 분석 성공분
            "collected_at":     started_at,           # AP-3
            "dropped_by_guard": dropped,
            "failed_chunks":    n - len(succeeded),            # CH-D10 Q4
            "missing_items":    len(items_sorted) - analyzed,  # CH-D10 Q4
        }
        if dropped:
            logger.warning("reaction_analysis: %s 가드 제거 %d건", cid, dropped)
        if n - len(succeeded):
            logger.warning("reaction_analysis: %s chunk %d/%d 실패 — 댓글 %d건 누락",
                           cid, n - len(succeeded), n, len(items_sorted) - analyzed)

    step = _step("completed", started_at)
    if errors:
        step["error_message"] = f"{len(errors)}건 부분 실패"
    total = sum(len(v["tuples"]) for v in reaction_analysis.values())
    logger.info("reaction_analysis: %d candidates · tuple %d건 (chunk 부분실패 %d)",
                len(reaction_analysis), total, len(errors))

    out: dict = {"reaction_analysis": reaction_analysis, "agent_steps": [step]}
    if errors:
        out["errors"] = errors
    return out


def _step(status: str, started_at: str, error_message: str = "") -> AgentStep:
    step: AgentStep = {
        "step_name":   "ReactionAnalysis",
        "status":      status,
        "started_at":  started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_message:
        step["error_message"] = error_message
    return step


def _error_out(started_at: str, message: str) -> dict:
    logger.error("reaction_analysis_node: %s", message)
    return {
        "errors": [{"node": "reaction_analysis_node", "error": message,
                    "timestamp": datetime.now(timezone.utc).isoformat()}],
        "agent_steps": [_step("failed", started_at, message)],
    }
