"""트랙별 검색어 — 후보 상품명·발급사·도메인 범주만으로 결정론 생성(같은 state → 같은 쿼리 → 캐시 히트)."""
from __future__ import annotations

from datetime import date

TRACKS = ("history", "finance", "market")


def candidates(state: dict) -> list[dict]:
    """[{cid, name, provider, is_own}] — 자사 먼저, 경쟁사는 id 정렬."""
    own = state.get("own_product") or {}
    own_id = own.get("product_id") or ""
    out = []
    for p in state.get("product_profiles") or []:
        cid = p.get("candidate_id")
        if not cid:
            continue
        out.append({"cid": cid, "name": p.get("product_name") or cid,
                    "provider": (own.get("provider") if cid == own_id else None) or "",
                    "is_own": cid == own_id})
    return sorted(out, key=lambda c: (not c["is_own"], c["cid"]))


def build_queries(state: dict, as_of: date) -> dict[str, list[dict]]:
    cands = candidates(state)
    own = next((c for c in cands if c["is_own"]), None)
    category = (state.get("own_product") or {}).get("category") or "트래블카드"
    y = as_of.year
    q: dict[str, list[dict]] = {t: [] for t in TRACKS}

    def add(track, cid, text):
        q[track].append({"qid": f"{track[0]}{len(q[track]) + 1:02d}", "cid": cid, "query": text})

    for c in cands:                                     # 이력: 변경·출시/연장·규제 특례
        add("history", c["cid"], f"{c['name']} 혜택 변경 수수료")
        add("history", c["cid"], f"{c['name']} 출시 개편 연장 종료")
        add("history", c["cid"], f"{c['name']} 혁신금융서비스")
    if own and own["provider"]:                         # 재무: 자사 발급사 지표 + 경쟁사 공표 지표
        add("finance", own["cid"], f"{own['provider']} 외화통장 좌수 돌파")
        add("finance", own["cid"], f"{own['provider']} 외화예금 잔액")
    for c in cands:
        if not c["is_own"]:
            add("finance", c["cid"], f"{c['name']} 가입자 발급 돌파")
    add("market", None, "해외 체크카드 이용액 점유율 여신금융협회")   # 시장: 규모·점유·수익성·만료
    add("market", None, f"{category} 시장 경쟁 {y}")
    add("market", None, "카드사 수익성 해외결제 비용 부담")
    add("market", None, f"{category} 혜택 종료 {y}년 말")
    return q
