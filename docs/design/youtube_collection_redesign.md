# YouTube 수집 로직 재설계 결정 사항

> - **상태**: CONFIRMED
> - **작성일**: 2026-06-13
> - **배경**: Phase 1 전환 전 수집 품질 실측 및 필터 검증을 통해 도출된 설계 변경

---

## 1. 변경 동기

기존 YouTube 수집 파이프라인은 hint 기반 URL 탐색 → feature 매핑 → 제한적 댓글 수집 구조였다.
전환 전 품질 실측(`scripts/measure_youtube_collection.py`) 결과를 바탕으로 아래 4개 Phase에 걸친 변경을 결정했다.

---

## 2. 확정된 변경 사항

### Phase 1 — URL 발견 방식 변경

| 항목    | 기존                                | 변경 후                   |
| ----- | --------------------------------- | ---------------------- |
| 검색 쿼리 | `search_query_hints` (수동 hint 조합) | `candidate_name` 직접 검색 |
| 기간 필터 | 없음                                | `publishedAfter` 최근 2년 |
| 정렬    | relevance                         | relevance (유지)         |

hint는 폐기한다. 커뮤니티 URL 탐색과 동일하게 candidate 이름으로 직접 검색한다.
기간 필터는 `publishedAfter` 파라미터로 적용하며, 정렬은 relevance를 유지한다. 트래블카드는 핵심 조건(환율·수수료)이 연 1회 이상 변경되고, 코로나 시기(2020~2022) 데이터 오염을 피하기 위해 2년으로 설정한다. 도메인별로 조정이 필요한 경우 config 파라미터로 관리한다.

### Phase 3 — feature_mapping_youtube_reactions_node 폐기

`feature_mapping_youtube_reactions_node`를 파이프라인에서 제거한다.
feature 매핑 단계에서 YouTube 영상을 feature별로 분류하는 로직은 불필요한 복잡도를 유발하며, 이후 candidate 단위 ABSA에서 aspect 분류가 수행되므로 중복이다.

### Phase 4 — 댓글 수집 상한 폐기

| 항목 | 기존 | 변경 후 |
|---|---|---|
| 영상 수집 상한 | feature당 top 2, candidate당 max 6 | 상한 없음 (전량 수집) |
| 댓글 수집 상한 | 100개/영상 → 필터 → top 30, candidate당 150 | 상한 없음 (페이징 전량) |

수집된 댓글은 아래 §3의 pre-filter를 통과한 건만 ABSA 입력으로 사용한다.

### Phase 5 — ABSA 부분 수정

`reaction_analysis_node`의 수집·집계 로직은 유지한다. 단, 아래 §5의 multi-candidate 문제 대응을 위해 ABSA 시스템 프롬프트에 타깃 candidate 제한 지시를 추가한다.

---

## 3. Pre-filter 설계 (v2 확정)

전량 수집된 댓글을 ABSA 입력으로 넣기 전에 2단계 필터를 적용한다.

### 3-1. 1단계: aspect 키워드 매칭

aspect_codebook의 각 aspect별 대표 키워드를 하나 이상 포함하는 댓글만 통과시킨다.

- 실측 기준 유지율: **48%** (7,529건 → 3,644건)
- 목적: ABSA codebook과 무관한 구독·응원·일반 반응 제거

### 3-2. 2단계: 순수 의문문 제거 (v2 신규)

1단계를 통과했더라도 모든 문장이 의문형인 단문은 제거한다.

**적용 조건:**
- 전체 길이 ≤ 100자
- `?` 또는 한국어 의문형 종결어미(`나요`, `인가요`, `될까요`, `건가요`, `하나요`, `있나요`, `있을까요`, `까요`, `ㄴ가요`) 존재
- 마침표·느낌표·개행으로 분리한 모든 문장이 의문형으로 끝남

**검증 결과:**
- 추가 제거: **596건** (v1 유지분 대비 16%)
- 최종 유지율: **40%** (7,529건 → 3,048건)
- 실제 오제외(서술문에 경험·의견 포함): **0건 (0.0%)**

순수 의문문은 키워드를 포함하더라도 경험·의견이 없어 ABSA 7-tuple 추출이 불가능하다.

---

## 4. Multi-candidate 영상 처리

### 4-1. 문제 정의

`candidate_name` 직접 검색(Phase 1) 전환 시, "트래블로그 vs 트래블월렛 비교"처럼 복수 상품을 다루는 영상이 여러 candidate 검색에서 동시에 반환된다. 현재 아키텍처는 이를 올바르게 처리하지 못해 두 가지 문제가 발생한다.

**문제 A — 동일 영상 중복 수집 (quota 낭비)**

`youtube_reaction_collection_node`는 candidate별로 독립적으로 `youtube_comment_threads(vid)`를 호출한다. 동일 `video_id`가 N개 candidate 검색에서 반환되면 댓글을 N회 중복 수집한다. `commentThreads.list`는 1 unit/page이므로 직접적인 quota 낭비다.

**문제 B — 댓글 attribution 오류 (ABSA 품질)**

`youtube_reaction_collection_node` 수집 루프에서 모든 댓글에 단일 `candidate_id`를 부여한다:

```python
selected_comments.append({**c, "candidate_id": cid})  # 검색 주체 candidate만 귀속
```

"트래블로그 ATM은 무료인데 트래블월렛은 수수료 있어요" 같은 댓글이 `comp_하나트래블로그카드` 검색으로 수집되면:
- `comp_트래블월렛` ABSA 입력에 이 댓글이 누락된다.
- `comp_하나트래블로그카드` ABSA에서는 트래블월렛에 대한 negative tuple이 잘못된 candidate에 귀속된다.

ABSA 시스템 프롬프트 확인 결과, 출력의 모든 tuple은 입력의 `candidate_id`로 고정(`"candidate_id": "<입력의 candidate_id 그대로>"`)되므로, 다른 상품 언급이 포함된 댓글의 tuple도 전부 단일 candidate에 귀속되는 구조다.

### 4-2. 해결 방안

**cross_reference_node 확장 — owned 필터링 + video_candidate_index 구축**

`cross_reference_node`는 이미 `youtube_reactions_urls_by_candidate` 전체를 순회하므로, owned 채널 필터링 루프 안에서 `video_id → [candidate_ids]` 역인덱스를 동시에 구축한다. 결과는 새 state 키 `video_candidate_index`로 저장한다.

```python
# cross_reference_node 출력 (확장 후)
{
  "youtube_reactions_urls_by_candidate": filtered,   # 기존 구조 유지
  "video_candidate_index": {
      "XXXXXXXXXXX": ["comp_트래블월렛", "comp_하나트래블로그카드"],
      "YYYYYYYYYYY": ["comp_트래블월렛"],
  }
}
```

`youtube_reactions_urls_by_candidate`의 기존 구조(`{candidate_id: [video_dicts]}`)를 유지하므로 `feature_selection_node`의 URL preview 집계 코드가 그대로 동작한다.

**수집 노드 변경 — video_candidate_index 직접 소비**

`youtube_reaction_collection_node`는 `video_candidate_index`를 직접 읽어 dedup 수집과 댓글 multi-tagging을 처리한다.

```
# 변경 전
for cid in candidates:
    for vid in videos[cid]:
        comments = collect(vid)          # 중복 수집
        tag(comments, candidate_id=cid)  # 단일 귀속

# 변경 후
for vid, cids in video_candidate_index.items():
    comments = collect(vid)              # 1회 수집 (dedup)
    for cid in cids:
        tag_and_append(comments, candidate_id=cid)  # 전체 candidate에 복제 (multi-tagging)
```

**ABSA 시스템 프롬프트 변경 — 타깃 candidate 제한 추가**

`agents/reaction_analysis/system_prompt_kr.md`의 추출 규칙에 아래를 추가한다:

> Items 안에 여러 상품이 언급될 수 있습니다. 반드시 `candidate_name`(입력의 `candidate_id`에 해당하는 상품)에 대한 의견만 추출하십시오. 다른 상품에 대한 의견은 tuple을 만들지 마십시오.

이 변경으로 복수 상품 언급 댓글에서 타깃 candidate 외 상품의 tuple이 오귀속되는 문제를 차단한다.

### 4-3. 커뮤니티 데이터와의 비교

커뮤니티 데이터도 구조적으로 동일한 문제를 안고 있다. 단, 커뮤니티 게시글은 특정 상품 중심으로 작성되는 경우가 많아 단일 게시글이 복수 candidate 검색에 동시에 반환될 가능성이 낮다. YouTube는 비교·랭킹·추천 형식의 영상이 많아 동일 영상이 여러 candidate 검색에서 중복 반환될 가능성이 높으므로 우선 대응한다.

---

## 5. 영상 본문(자막) 수집 — 잠정 중단

### 5-1. 검토 배경

YouTube 영상 본문을 ABSA 입력 텍스트로 추가 활용하기 위해 `youtube-transcript-api` 기반 자막 수집을 검토했다.

### 5-2. 검토 결과

| 항목                       | 결과                                              |
| ------------------------ | ----------------------------------------------- |
| 수동 자막(creator 직접 업로드) 비율 | **4.5%** (398건 중 약 18건)                         |
| 자동 생성 자막 품질              | STT 오인식 다수 (상품명·핵심 키워드 포함)                      |
| API 비용                   | 없음 (youtube-transcript-api는 Data API quota 미소비) |

**STT 오인식 주요 패턴 (6건 샘플 기준):**

| 오인식 원문 | 실제 의미 | 유형 |
|---|---|---|
| 수술 | 수수료 | 핵심 키워드 |
| 한율 로대 | 환율 우대 | 핵심 키워드 |
| 트래블럴렛 / 트래블 원렛 | 트래블월렛 | 상품명 |
| 신앙카드 | 신한카드 | 상품명 |
| 한화 카드 | 하나카드 | 상품명 |
| 수수료가 영원인 | 수수료가 0원인 | 핵심 수치 |

### 5-3. 중단 근거

1. **수동 자막 4.5% — 절대 수량 부족**: 실제 분석에서 candidate당 수백~수천 건의 영상을 수집하더라도 수동 자막 보유 영상은 극소수다. 소수 영상 본문만으로 유의미한 ABSA 결과를 만들기 어렵다.

2. **자동 자막 품질 불신뢰**: 상품명과 핵심 키워드가 오인식되면 LLM의 candidate 귀속 및 aspect 분류가 부정확해진다. 자동 자막은 ABSA 직접 입력으로 부적합하다.

3. **수집 시간 대비 실익 없음**: `api.list()` 호출당 ~0.3초 소요 시 수백 건 처리에 수 분이 소요된다. 영상 수집 규모가 커질수록 이 비용은 선형 증가한다.

### 5-4. 향후 재검토 조건

아래 조건이 충족되면 재검토한다:

- 특정 도메인에서 수동 자막 보유율이 20% 이상으로 확인된 경우
- YouTube가 수동 자막 기반의 공식 Captions API를 quota 없이 제공하는 경우
- STT 후처리(상품명 사전 기반 교정) 로직이 준비된 경우

---

## 6. 구현 대상 요약

### 6-1. 구현 순서

| 순서  | Phase   | 변경 내용                                                                                                    | 구현 파일                                            |
| --- | ------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| 1   | 공통      | `video_candidate_index` state 키 추가, `youtube_reactions_raw_features` 키 deprecated 표시                     | `server/graph/state.py`                          |
| 2   | Phase 1 | `youtube_search_videos`에 `published_after` 파라미터 추가, `nextPageToken` 페이지네이션 구현, 캐시 키 `v:2`로 버전 업          | `server/llm/youtube_client.py`                   |
| 3   | Phase 1 | hint 폐기 → candidate_name 검색, publishedAfter 2년, 동일 candidate 내 video_id dedup                            | `url_discovery_youtube_reactions_node.py`        |
| 4   | Phase 4 | owned 필터링 유지 + video_candidate_index 구축 (필터 통과 영상 기준, set으로 candidate_id 중복 방지)                          | `cross_reference_node.py`                        |
| 5   | Phase 4 | video_candidate_index 직접 소비, 수집 상한 폐기, pre-filter v2 적용, multi-tagging, collected_videos candidate_id 제거 | `youtube_reaction_collection_node.py`            |
| 6   | Phase 3 | feature_mapping_youtube_reactions_node 노드·엣지 제거, `youtube_reactions_raw_features` 키 삭제                   | `server/graph/graph.py`, `server/graph/state.py` |
| 7   | Phase 5 | ABSA 프롬프트 타깃 candidate 제한 추가                                                                             | `agents/reaction_analysis/system_prompt_kr.md`   |

순서 1·2가 완료되어야 3번 구현이 가능하다. 1번이 완료되어야 4·5번 구현이 가능하다. 6번(graph 토폴로지 + state 정리)은 5번 완료 후 진행한다.

**Known Issue — reaction_insight 카드 미렌더 (위협 3)**

`feature_selection_node`는 `analysis_features`에 reaction_insight feature가 1건 이상 있어야 카드를 렌더한다. youtube 경로 feature_mapping 제거 후 community 수집 결과가 0건이면 reaction_insight 카드가 미렌더되어 사용자가 선택할 수 없다. 현재 커뮤니티 수집이 활성화된 환경에서는 발생하지 않으므로 별도 대응 없이 known issue로 기록한다. community 비활성 시나리오가 생기면 `feature_selection_node`에서 `youtube_reactions_urls_by_candidate` 존재 여부로 카드 표시 여부를 독립적으로 판단하는 로직 추가가 필요하다.

### 6-2. 영상 본문(자막) 수집 잠정 중단

별도 구현 없음. §5 참조.

---

## 7. 관련 산출물

| 파일 | 설명 |
|---|---|
| `scripts/measure_youtube_collection.py` | 수집 품질 실측 스크립트 |
| `scripts/validate_youtube_prefilter.py` | pre-filter v1/v2 비교 검증 스크립트 |
| `scripts/check_manual_captions.py` | 자막 유형(수동/자동) 분포 확인 스크립트 |
| `scripts/diagnose_transcript.py` | youtube-transcript-api 실패 원인 진단 스크립트 |
| `data/measurement/youtube_collection_3_slug_20260613T084051.json` | 기준 측정 산출물 (4 candidates, 398 videos) |
| `data/measurement/..._filtered_v2.json` | pre-filter v2 적용 결과 (3,048건) |
