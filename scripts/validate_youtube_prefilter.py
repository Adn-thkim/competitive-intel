#!/usr/bin/env python3
"""
scripts/validate_youtube_prefilter.py  (v0.3 — YR-D3 대댓글 키워드 면제 재측정)
----------------------------------------------
measure_youtube_collection.py 산출물에 키워드 pre-filter를 적용하고
false negative / precision 샘플을 출력해 필터 품질을 검증한다.

필터 단계:
  1단계: 키워드 매칭 (aspect 관련 텍스트 선별)
  2단계: 순수 의문문 제거 (모든 문장이 의문형인 단문 — ABSA 입력 가치 없음)
  YR-D3(v3): 부모(최상위)가 aspect 키워드를 가진 스레드의 대댓글은 키워드 면제

재측정: v2(균일 적용) 대비 v3(YR-D3)의 유지율/오제외율 변화를 비교 출력한다.
대댓글 데이터가 있어야(measure 가 Phase A 로 수집) 의미가 있으며, 없으면 v3==v2.

실행
----
  cd competitive-intel
  python -m scripts.validate_youtube_prefilter \\
      data/measurement/youtube_collection_3_slug_20260613T084051.json

출력
----
  stdout : per-candidate 통계 + v1/v2 비교 + 샘플 테이블
  data/measurement/{stem}_filtered.json : 최종 통과 댓글만 포함한 JSON
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

# ── 키워드 사전 (v1 — 1차 검증 대상) ──────────────────────────────────────────
# 각 aspect별 매칭 키워드. 제거된 댓글 샘플을 보고 확장/수정한다.
ASPECT_KW: dict[str, list[str]] = {
    "overseas_payment_convenience": [
        "해외결제", "해외 결제", "결제 안됨", "결제오류", "결제 오류", "해외에서",
        "애플페이", "구글페이", "삼성페이", "GLN", "gln", "QR결제", "비자", "마스터",
        "페이", "결제", "overseas",
    ],
    "exchange_rate_fairness": [
        "환율", "환전 수수료", "수수료", "환전",
        "달러", "엔화", "유로", "위안", "원화", "외화",
        "실시간 환율", "우대환율", "환율 우대",
    ],
    "atm_withdrawal_ux": [
        "ATM", "atm", "현금인출", "현금 인출", "출금",
        "현금", "인출", "CD기", "자동화기기",
    ],
    "app_ux_quality": [
        "앱", "어플", "앱이", "앱에서", "인터페이스",
        "토스", "앱 오류", "앱 버그", "앱 업데이트", "UI", "UX",
        "알림", "설정", "로그인",
    ],
    "emergency_card_lock": [
        "잠금", "분실", "카드 정지", "카드잠금", "분실신고",
        "도난", "카드 해지", "일시정지", "해외 분실",
    ],
    "pricing_perception": [
        "수수료", "비용", "요금", "이용료", "연회비",
        "무료", "유료", "과금", "청구", "가격", "요금제",
    ],
    "travel_benefit_value": [
        "혜택", "마일리지", "마일", "포인트", "라운지",
        "캐시백", "적립", "할인", "무료 제공", "특전",
        "여행자 보험", "보험",
    ],
    "customer_support": [
        "고객센터", "상담", "CS", "문의",
        "콜센터", "전화", "답변", "응대", "처리",
    ],
    "fx_reload_convenience": [
        "충전", "환전하기", "재충전", "잔액",
        "계좌", "이체", "입금", "송금", "환전소",
    ],
}

ALL_KW: list[str] = [kw for kws in ASPECT_KW.values() for kw in kws]

# ── 순수 의문문 제거 (2단계 필터) ─────────────────────────────────────────────
# 한국어 의문형 종결어미 + 영문 ?
_QUESTION_END = re.compile(
    r'나요\s*\??$|'
    r'인가요\s*\??$|'
    r'될까요\s*\??$|'
    r'건가요\s*\??$|'
    r'하나요\s*\??$|'
    r'있나요\s*\??$|'
    r'있을까요\s*\??$|'
    r'[까]\s*요?\s*\??$|'
    r'ㄴ가요\s*\??$|'
    r'\?\s*$',
)
# 문장 분리 기준 (마침표·느낌표·개행)
_SENT_SPLIT = re.compile(r'[.!。\n]+')

_PURE_QUESTION_MAX_LEN = 100  # 이 이상이면 의견+질문 혼합 가능성 → 통과


def _is_pure_question(text: str) -> bool:
    """모든 문장이 의문형인 단문이면 True.

    조건:
      - 전체 길이 ≤ _PURE_QUESTION_MAX_LEN (긴 텍스트는 의견 포함 가능)
      - ? 또는 의문형 종결어미가 존재
      - 마침표·느낌표로 분리한 모든 문장이 의문형 종결
    """
    text = text.strip()
    if not text or len(text) > _PURE_QUESTION_MAX_LEN:
        return False
    if not _QUESTION_END.search(text):
        return False
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    return bool(sentences) and all(_QUESTION_END.search(s) for s in sentences)


def _hit_aspects(text: str) -> list[str]:
    """텍스트에 매칭된 aspect_id 목록 반환."""
    return [aid for aid, kws in ASPECT_KW.items() if any(kw in text for kw in kws)]


def _apply_filter_v1(comments: list[dict]) -> tuple[list[dict], list[dict]]:
    """v1: 키워드 매칭만."""
    kept, removed = [], []
    for c in comments:
        if _hit_aspects(c.get("text", "")):
            kept.append(c)
        else:
            removed.append(c)
    return kept, removed


def _apply_filter_v2(comments: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """v2: 키워드 매칭 → 순수 의문문 제거.

    Returns
    -------
    (kept, removed_no_kw, removed_pure_question)
    """
    kept, removed_no_kw, removed_pq = [], [], []
    for c in comments:
        text = c.get("text", "")
        if not _hit_aspects(text):
            removed_no_kw.append(c)
        elif _is_pure_question(text):
            removed_pq.append(c)
        else:
            kept.append(c)
    return kept, removed_no_kw, removed_pq


def _apply_filter_v3(comments: list[dict]) -> tuple[list[dict], list[dict]]:
    """v3 = v2 + YR-D3: 부모(최상위)가 aspect 키워드를 가진 스레드의 대댓글은 키워드 면제.

    순수 의문문은 대댓글에도 적용. thread_id/is_reply 없는 구 데이터면 v3 == v2.

    Returns
    -------
    (kept, exempted)
      exempted: YR-D3로 새로 통과한 대댓글(키워드 없으나 부모 통과) — v2 대비 증가분.
    """
    passing_threads = {
        c.get("thread_id", "")
        for c in comments
        if not c.get("is_reply") and _hit_aspects(c.get("text", ""))
    }
    kept, exempted = [], []
    for c in comments:
        text = c.get("text", "")
        if _is_pure_question(text):
            continue
        if c.get("is_reply") and c.get("thread_id", "") in passing_threads:
            kept.append(c)
            if not _hit_aspects(text):
                exempted.append(c)   # 키워드 없이 부모 통과로만 들어온 항목
            continue
        if not _hit_aspects(text):
            continue
        kept.append(c)
    return kept, exempted


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

def _truncate(text: str, width: int = 90) -> str:
    text = text.replace("\n", " ").strip()
    return text[:width] + "…" if len(text) > width else text


def _print_sample(comments: list[dict], n: int, label: str, seed: int = 0) -> None:
    rng = random.Random(seed)
    sample = rng.sample(comments, min(n, len(comments)))
    print(f"\n  [{label}] {len(sample)}건 샘플:")
    for c in sample:
        text = _truncate(c.get("text", ""))
        aspects = _hit_aspects(c.get("text", ""))
        aspect_tag = f"  ← {', '.join(aspects)}" if aspects else ""
        print(f"    • {text}{aspect_tag}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube pre-filter 키워드 검증 (v1 vs v2 비교)")
    parser.add_argument("input", type=Path,
                        help="measure_youtube_collection.py 산출 JSON 경로")
    parser.add_argument("--sample-pq", type=int, default=20,
                        help="순수 의문문 제거 샘플 수 (default=20)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    codebook = data.get("aspect_codebook") or []
    label_map = {a["aspect_id"]: a.get("label", a["aspect_id"]) for a in codebook}
    results = data["results"]

    # ── per-candidate 비교 ────────────────────────────────────────────────────
    summary_rows: list[dict] = []

    for cid, r in results.items():
        all_comments: list[dict] = []
        for v in r.get("videos") or []:
            all_comments.extend(v.get("comments") or [])
        total = len(all_comments)

        replies = [c for c in all_comments if c.get("is_reply")]

        kept_v1, _      = _apply_filter_v1(all_comments)
        kept_v2, _, pq  = _apply_filter_v2(all_comments)
        kept_v3, exempt = _apply_filter_v3(all_comments)

        summary_rows.append({
            "cid":     cid,
            "total":   total,
            "replies": len(replies),
            "v1":      len(kept_v1),
            "v2":      len(kept_v2),
            "v3":      len(kept_v3),
            "exempt":  len(exempt),   # YR-D3 면제로 새로 통과한 대댓글
            "pq":      len(pq),       # 순수 의문문 제거 수
        })

        print(f"\n{'═'*65}")
        print(f"  {cid}  (전체 {total}건 · 대댓글 {len(replies)}건)")
        print(f"  v1 유지: {len(kept_v1):>4}건 ({len(kept_v1)/total:.0%})  "
              f"v2 유지: {len(kept_v2):>4}건 ({len(kept_v2)/total:.0%})  "
              f"v3 유지: {len(kept_v3):>4}건 ({len(kept_v3)/total:.0%})  "
              f"│  순수 의문문 제거: {len(pq)}건  │  YR-D3 면제: {len(exempt)}건")
        if exempt:
            _print_sample(exempt, args.sample_pq, "YR-D3 키워드 면제 대댓글 샘플(오포함 검토용)", args.seed)

        # aspect별 v1/v2 비교
        print(f"\n  {'aspect':<22}  {'v1':>5}  {'v2':>5}  {'차이':>5}")
        print(f"  {'─'*22}  {'─'*5}  {'─'*5}  {'─'*5}")
        for aid, kws in ASPECT_KW.items():
            c1 = sum(1 for c in kept_v1 if any(kw in c.get("text","") for kw in kws))
            c2 = sum(1 for c in kept_v2 if any(kw in c.get("text","") for kw in kws))
            label = label_map.get(aid, aid)
            print(f"  {label:<22}  {c1:>5}  {c2:>5}  {c2-c1:>+5}")

        # 순수 의문문 샘플 출력
        if pq:
            _print_sample(pq, args.sample_pq, "순수 의문문 제거 샘플", args.seed)

    # ── 전체 합산 비교 ─────────────────────────────────────────────────────────
    g_total   = sum(r["total"]   for r in summary_rows)
    g_replies = sum(r["replies"] for r in summary_rows)
    g_v1      = sum(r["v1"]      for r in summary_rows)
    g_v2      = sum(r["v2"]      for r in summary_rows)
    g_v3      = sum(r["v3"]      for r in summary_rows)
    g_exempt  = sum(r["exempt"]  for r in summary_rows)
    g_pq      = sum(r["pq"]      for r in summary_rows)

    print(f"\n{'═'*72}")
    print(f"  전체 합산 ({g_total}건 · 대댓글 {g_replies}건)")
    print(f"  {'candidate':<26}  {'전체':>5}  {'v2':>5}  {'v2%':>4}  {'v3':>5}  {'v3%':>4}  {'면제':>4}")
    print(f"  {'─'*26}  {'─'*5}  {'─'*5}  {'─'*4}  {'─'*5}  {'─'*4}  {'─'*4}")
    for r in summary_rows:
        print(f"  {r['cid']:<26}  {r['total']:>5}  {r['v2']:>5}  "
              f"{r['v2']/r['total']:.0%}  {r['v3']:>5}  "
              f"{r['v3']/r['total']:.0%}  {r['exempt']:>4}")
    print(f"  {'합계':<26}  {g_total:>5}  {g_v2:>5}  "
          f"{g_v2/g_total:.0%}  {g_v3:>5}  "
          f"{g_v3/g_total:.0%}  {g_exempt:>4}")

    # ── YR-D3 재측정 결론 ─────────────────────────────────────────────────────
    print(f"\n  ── YR-D3 재측정 (오제외율/유지율 변화) ──")
    if g_replies == 0:
        print("  ⚠ 입력에 대댓글(is_reply)이 없습니다 — Phase A 적용 후 measure 재실행 필요.")
        print("    현재 데이터로는 v3 == v2 (면제 0건). 재측정 불가.")
    else:
        d_ret = (g_v3 - g_v2) / g_total
        print(f"  유지율: v2 {g_v2/g_total:.1%} → v3 {g_v3/g_total:.1%}  (+{d_ret:.1%}p)")
        print(f"  YR-D3 면제(키워드 없이 부모 통과한 대댓글): {g_exempt}건")
        print(f"  v3는 v2 대비 keep만 추가(완화)하므로 오제외(false negative)는 v2 이하.")
        print(f"  ※ 오포함 위험은 위 '면제 대댓글 샘플'을 육안 검토해 판정.")

    # ── v2 필터 적용 결과 JSON 저장 ───────────────────────────────────────────
    filtered_results: dict = {}
    for cid, r in results.items():
        filtered_videos = []
        for v in r.get("videos") or []:
            kept, _ = _apply_filter_v3(v.get("comments") or [])
            filtered_videos.append({**v, "comments": kept})
        total_kept = sum(len(v["comments"]) for v in filtered_videos)
        filtered_results[cid] = {
            **{k: v for k, v in r.items() if k not in ("videos", "comment_total")},
            "comment_total":    total_kept,
            "comment_filtered": r.get("comment_total", 0) - total_kept,
            "videos":           filtered_videos,
        }

    out_path = args.input.parent / (args.input.stem + "_filtered_v3.json")
    out_data = {**data, "results": filtered_results,
                "filter_meta": {
                    "version":        "v3",
                    "yr_d3":          "부모 통과 스레드의 대댓글 키워드 면제",
                    "pure_q_max_len": _PURE_QUESTION_MAX_LEN,
                    "kw_counts":      {aid: len(kws) for aid, kws in ASPECT_KW.items()},
                }}
    out_path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(f"\n  v3 저장: {out_path}")


if __name__ == "__main__":
    main()
