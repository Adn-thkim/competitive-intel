# 반응 파이프라인 이식 절차 — Phase 1(코어 server 이전) · Phase 2(노드 완성) · 검증

> 통합계획(`reaction_report_integration_plan.md`) §4-2의 실행 절차. **원칙**: verbatim 이전
> (재작성 아님 — 로직 불변) + **각 단계 compile → 순수 단위 → 소량 온라인** 검증 후 다음. LLM 호출은
> 사용자 머신에서만 검증 가능하므로 단계마다 소량 표본으로 확인한다.

## Phase 1 — 코어 로직 `server/graph/reaction/` 정본 이전 (DRY 역-import)

각 모듈 이전 후 **scripts의 원 정의를 삭제하고 server를 re-import** → 중복 제거 + dev 재현성 유지.
프로덕션 노드는 `server.graph.reaction.*`만 참조(scripts 미참조).

| 순서 | 모듈 | 이전 대상(원본) | 검증 |
|---|---|---|---|
| 1a ✅ | `llm.py` | test_gate_prescreen_v2: `_cli·_pmap·_MODELS` + poc_opinion_units: `_ABORT·_is_quota_exhausted` | compile + 단위(`pmap`·`is_quota_exhausted`·`cli` import) — **완료** |
| 1b | `gate.py` | test_gate_prescreen_v2: `gate_videos·_GATE_SYS·_GATE_SCHEMA` + test_stage1_v2: `build_domain_signals·video_gate` + poc_opinion_units_community: `community_gate_sys` | compile + stub 게이트(휴리스틱) + 소량 온라인(영상 5·게시글 5 통과율) |
| 1c | `extract.py` | poc_opinion_units: `_SYS·스키마·extract·candidate_context·build_keywords·_split_by_chars` + poc_opinion_units_community: `build_threads·load_community_posts·_cand_label` + 제목 멤버·dedup addendum | compile + 순수 단위(스레드 조립·태깅·dedup 문자열) + 소량 온라인(스레드 3 추출) |
| 1d | `cluster.py` | poc_cluster_uq_v2 + poc_bottomup_cluster(`cluster_terms·normalize·super_cluster`) + poc_cluster_units_questions(`map_questions·assign_question_type`) + `assign_perspective` + 병합·오케스트레이션 | compile + 소량 온라인(units 50 군집 → 축·기타율) + 축 저장소 왕복 |

**이전 방식(모듈당):** ① 함수 정의를 server 모듈로 복사(verbatim) → `from server.graph.reaction.llm import ...`로 내부 참조 교체 → ② `python -m py_compile` → ③ 순수 단위 테스트 → ④ scripts 원본을 `from server.graph.reaction.X import ...`로 교체(정의 삭제) → ⑤ scripts 재실행으로 회귀 없음 확인.

## Phase 2 — 노드 오케스트레이션 완성 + import 교체

| 노드 | 완성 작업 | import 교체 |
|---|---|---|
| `reaction_video_gate_node` | (완성) — `server.gate.gate_videos` 사용 | scripts → server.gate |
| `reaction_prescreen_node` | youtube Stage0/1 스레드 조립(`server.extract` 헬퍼 + selected_comments/youtube_comments 캐시) | scripts → server |
| `reaction_extract_node` | **youtube 경로 스레드 조립 추가**(community 경로는 이미 있음) → 양 채널 units/questions | scripts → server.extract·gate |
| `reaction_cluster_node` | `server.cluster` 오케스트레이션(병합 → Phase A 도출/Phase B 배정) + 축 저장소 연동 | scripts → server.cluster |

## 실행 및 검증 과정 (단계별)

### A. 오프라인 (이 저장소 — 네트워크 불필요)
```bash
# 1) 컴파일
python -m py_compile server/graph/reaction/*.py server/graph/nodes/reaction_*_node.py
# 2) 순수 단위 (예: 스레드 조립·태깅·dedup·축 저장소)
python -c "from server.graph.reaction.llm import pmap, is_quota_exhausted; assert pmap(lambda x:x+1,[1,2],2)==[2,3]"
python -c "from server.graph.reaction_axis_store import axis_key, store_axis, load_axis; k=axis_key('d',['a']); store_axis(k,{'x':{'y':1}}); assert load_axis(k)['key']==k"
```
**합격 기준**: 전부 오류 0. 노드가 scripts 를 import 하지 않는지 `grep -rn 'import scripts' server/graph/` == 0.

### B. 소량 온라인 (사용자 머신 — LLM 필요)
각 노드를 **state 소량 표본**으로 단독 호출해 산출 키만 확인(그래프 미배선 상태로도 가능):
```python
import json; from server.graph.nodes.reaction_extract_node import reaction_extract_node
state = json.load(open('data/debug/reaction_state.json'))          # 전량 대신 표본으로 슬라이스 권장
out = reaction_extract_node(state, {'configurable':{'thread_id':'t'}})
u,q = out['reaction_units'], out['reaction_questions']
print(len(u), len(q))
assert all('channel' in x and 'source_site' in x for x in u)       # 태깅 검증
```
**합격 기준(노드별)**:
- gate: `reaction_gate.kept_video_ids` 비율이 표본 게이트와 일치.
- extract: `reaction_units`/`reaction_questions` 비어있지 않고 `channel·source_site·quote·target_term` 채움. 제목發 question 회수 확인.
- cluster: `reaction_axis`(super_to_sub) + `reaction_clustered`, **기타율 <5%**, sub→super 불일치 0, **channel/source_site 보존**.

### C. 통합 (graph 배선 §4-4 이후)
전체 데이터 e2e 1회 실행 → 추출은 청크 캐시(agent_cache) 채움 → 이후 **리포트는 캐시만 read**(재수집·재추출 없음). 신·구 병행 중이므로 구 `reaction_analysis` 리포트와 산출 대조.

## 위험·원칙
- **verbatim 이전**(로직 재작성 금지) → LLM 프롬프트·스키마 불변, 회귀 위험 최소.
- 모듈/노드 **1개씩** 이전·검증 후 다음(빅뱅 금지). B(소량 온라인)에서 실패 시 그 단계에서 멈춘다.
- 필드명 정규화(PoC `source_site`+`video_id` ↔ 프로덕션 `source_url`)는 extract 이전 시 확정(§6-4).
