# scripts/verify_fanin.py — 본 적용 전 단독 검증
from langgraph.graph import END, START, StateGraph
from typing import TypedDict
import time

class S(TypedDict, total=False):
    a_done: bool
    b_done: bool
    join_count: int

def a(state):
    time.sleep(0.5)            # 분기 A: 느림 (실제 interrupt 모사)
    return {"a_done": True}

def b(state):
    return {"b_done": True}    # 분기 B: 즉시 완료 (캐시 적중 모사)

def join(state):
    cnt = (state.get("join_count") or 0) + 1
    print(f"join() called #{cnt} — a={state.get('a_done')} b={state.get('b_done')}", flush=True)
    return {"join_count": cnt}

g = StateGraph(S)
g.add_node("a", a); g.add_node("b", b); g.add_node("join", join)
g.add_edge(START, "a"); g.add_edge(START, "b")
g.add_edge(["a", "b"], "join")
g.add_edge("join", END)

final = g.compile().invoke({})
assert final["join_count"] == 1, f"join executed {final['join_count']} times (expected 1)"
print("✅ list-based fan-in barrier verified")