# Baseline 측정 절차 — B1 ~ B7 지표 (v0.10.17 시점)

> - **목적**: v0.10.18 ~ v0.10.27 PR 의 모든 검증 게이트가 비교 기준으로 사용할 baseline 7종 지표(B1 ~ B7) 를 결정론적으로 측정
> - **선행 문서**: `docs/design/feature_url_mapper_redesign.md` v0.3 §11 (검증 게이트 정의)
> - **자동 측정 스크립트**: `scripts/measure_baseline.py`
> - **결과 저장 위치**: `docs/baseline_v0_10_17.md`
> - **예상 소요 시간**: 자동 측정 1분 + B5·B6 수동 측정 20~40분
> - **작성일**: 2026-06-02

---

## 1. 측정 대상 7종 지표 개요

| ID  | 지표                                                                 | 측정 방식 | 비교 검증 게이트                                        |
| :-: | ------------------------------------------------------------------ | :---: | ------------------------------------------------ |
| B1  | reaction\_insight comp_* `coverage="not_found"` 비율                 |  자동   | v0.10.23 < 50%                                   |
| B2  | reaction\_insight comp_* `existing_urls` 외부 host 비율                |  자동   | v0.10.23 ≥ 75%                                   |
| B3  | reaction_insight 전체 `additional_urls` 의 `validated=True` 비율        |  자동   | Phase 3 ≥ 30%                                    |
| B4  | reaction\_insight comp_* `candidate_coverage` 평균 `existing_urls` 수 |  자동   | v0.10.27 비교                                      |
| B5  | LLM 호출 수 (cache miss 첫 실행)                                         |  수동   | v0.10.27 = 8회                                    |
| B6  | 4단계 wall-clock cache miss 합산                                       |  수동   | v0.10.27 ≤ 17분                                   |
| B7  | report_type 별 features 분포                                          |  자동   | v0.10.18: positioning_map · executive_summary 0건 |

자동 측정 5종(B1·B2·B3·B4·B7) 은 `scripts/measure_baseline.py` 가 캐시 엔트리 분석만으로 산출합니다. 수동 측정 2종(B5·B6) 은 cache miss. 실 실행을 요구합니다.

---

## 2. 사전 점검 (5분)

### 2-1. 환경 확인

```bash
# 1. workspace 진입
cd /Users/thkim/Documents/Claude/Projects/ci-workspace/competitive-intel

# 2. Python 3.10+ 확인 (3.10 이상 권장)
python3 --version

# 3. main 브랜치 동기화
git checkout main
git pull origin main
git status   # working tree clean 확인
```

### 2-2. 캐시 파일 존재 확인

```bash
# 자동 측정 대상 캐시
test -f data/cache/agent_outputs/feature_url_mapper.json && echo "✓ feature_url_mapper.json 존재" || echo "✗ 없음"
test -f data/cache/agent_outputs/official_source_resolver.json && echo "✓ official_source_resolver.json 존재" || echo "✗ 없음"
```

두 파일 모두 존재해야 합니다. 없으면 파일럿 도메인(`핀테크 / 해외여행 특화 카드`) 분석을 한 번 실행하여 캐시 생성 후 재진입.

### 2-3. 측정 대상 엔트리 prefix 확인 (선택)

기본 prefix 는 `d4a2cba9` 입니다. 다른 엔트리를 측정하려면:

```bash
python3 << 'EOF'
import json
c = json.loads(open('data/cache/agent_outputs/feature_url_mapper.json').read())
for k, e in c['entries'].items():
    feats = e.get('output', {}).get('features', [])
    from collections import Counter
    rt = Counter(f.get('report_type') for f in feats)
    print(f"{k[:16]}.. created={e.get('created_at','')[:19]} hits={e.get('hit_count')} #feats={len(feats)} report_types={dict(rt)}")
EOF
```

가장 많은 hits + 최근 created_at + features 55개 + 7종 report_type 모두 보유 엔트리를 선택합니다(현재 d4a2cba9).

### 2-4. 측정 branch 생성

```bash
git checkout -b docs/baseline-v0_10_17
```

---

## 3. 자동 측정 (1분) — B1·B2·B3·B4·B7

### 3-1. 스크립트 실행

```bash
# 기본 실행 (d4a2cba9 prefix → docs/baseline_v0_10_17.md)
python3 scripts/measure_baseline.py

# 또는 dry-run 으로 결과만 확인 후 저장
python3 scripts/measure_baseline.py --dry-run | tee /tmp/baseline_preview.md
python3 scripts/measure_baseline.py   # 확인 후 정식 저장
```

### 3-2. stderr 요약 출력 해석

```
📂 캐시 로드: data/cache/agent_outputs/feature_url_mapper.json
   → 엔트리: d4a2cba937f37cff... (hit_count=3)
📂 공식 host 추출 (1차: feature_url_mapper / 2차 fallback: ...)
   → 8개 host
🔬 features 분석: 55개

============================================================
Baseline 측정 결과
============================================================
B1 reaction_insight comp_* not_found 비율:            81.0%  (17/21)
B2 reaction_insight comp_* 외부 host 비율:             0.0%  (0/4)
B3 reaction_insight additional_urls validated 비율:    0.0%  (0/42)
B4 candidate_coverage 평균 existing_urls 수:           0.19  (sum=4)
B7 report_type 분포: {'comparison_matrix': 10, ..., 'executive_summary': 8}
B5·B6: 외부 측정 필요 — docs/baseline_v0_10_17.md §2 참조
```

각 지표의 정확한 의미는 §1 표와 `docs/design/feature_url_mapper_redesign.md` §11 참조.

### 3-3. 결과 파일 검증

```bash
test -f docs/baseline_v0_10_17.md && echo "✓ 저장됨" || echo "✗ 저장 실패"
head -40 docs/baseline_v0_10_17.md
```

---

## 4. 결과 해석 — 7종 지표의 의미

### B1 — reaction_insight comp_* coverage="not_found" 비율

자사가 아닌 경쟁사(`comp_*`) candidate 가 reaction_insight feature 에 대해 어느 비율로 `not_found` 판정을 받았는지. 

- baseline 81% 의 의미: comp_* candidate 의 reaction_insight feature 21건 중 17건이 `existing_urls` 가 비어 있고 `additional_urls` 만 추정됨 → 외부 후기 수집이 사실상 안 됨
- 개선 후 < 50% 목표: v0.10.23 의 source-type 별 system_prompt + v0.10.20 의 YouTube reactions API + v0.10.21 의 owned channels 가 결합되면 외부 후기 URL 이 잡혀 not_found 가 줄어듦

### B2 — reaction_insight comp_* existing_urls 외부 host 비율

reaction_insight 의 comp_* candidate 가 보유한 existing_urls 중 비공식 host(`card-gorilla.com`·`brunch.co.kr`·`clien.net` 등) 의 비율.

- baseline 0% (0/4) 의 의미: comp_* 의 existing_urls 4건이 모두 공식 host(`hanacard.co.kr`·`travel-wallet.com` 등) → 외부 후기 도메인이 사실상 채택되지 않음
- 개선 후 ≥ 75% 목표: v0.10.23 의 reaction_insight system_prompt 가 외부 도메인 적극 채택 정책 적용 + v0.10.19 의 `url_discovery_blog_community_node` 가 외부 도메인 검색 적극

### B3 — reaction_insight additional_urls validated=True 비율

reaction_insight 의 모든 additional_urls 중 v0.10.9 `additional_urls_validation_node` 가 도달성 검증을 통과한 URL 비율.

- baseline 0% (0/42) 의 의미: LLM 이 공식 sub-page 만 추정했고, 그 추정 URL 들이 대부분 실재하지 않아 ✗ 404 처리됨 (캐시 검증 단계에서 None 처리)
- 개선 후 ≥ 30% 목표: v0.10.23 system_prompt 가 외부 도메인 허용 + v0.10.25 validation 분기로 source-type 별 정확한 검증

### B4 — reaction_insight comp_* candidate_coverage 평균 existing_urls 수

`comp_*` candidate 의 reaction_insight feature 에 대해 평균 몇 개의 existing_urls 가 잡혔는지.

- baseline 0.19 의 의미: 21건 candidate_coverage 에 existing_urls 총 4건 = 평균 0.19개. 사실상 비어 있음
- v0.10.27 비교 baseline: 5중 fan-out + source-type 별 system_prompt 적용 후 평균값 증가 추세 확인

### B7 — report_type 별 features 분포

domain_taxonomy 가 7종 리포트 모두에 features 를 채웠는지 또는 흐름 B-only 리포트(positioning_map·executive_summary) 가 빠졌는지 확인.

- baseline: 7종 모두 features 채워짐 — B-only 리포트도 URL 매핑 강제 수행 (turn-3 진단 문제 4번)
- v0.10.18 목표: positioning_map·executive_summary 의 features 가 0건이 되어야 함 (`source_flow="B"` 필터 적용 결과)

---

## 5. 수동 측정 (B5·B6) — 20~40분

### 5-1. 사전 준비 — 캐시 백업

cache miss 를 강제하기 위해 현재 캐시를 백업합니다. 측정 후 복원합니다.

```bash
mkdir -p /tmp/baseline_cache_backup
cp data/cache/agent_outputs/feature_url_mapper.json /tmp/baseline_cache_backup/
cp data/cache/agent_outputs/url_discovery_brave.json /tmp/baseline_cache_backup/ 2>/dev/null || true
cp data/cache/agent_outputs/page_meta_collect.json /tmp/baseline_cache_backup/ 2>/dev/null || true
cp data/cache/agent_outputs/url_validation.json /tmp/baseline_cache_backup/ 2>/dev/null || true
```

### 5-2. cache miss 강제 옵션 선택

#### 옵션 A — 캐시 파일 임시 제거 (가장 간단)

```bash
rm data/cache/agent_outputs/feature_url_mapper.json
rm data/cache/agent_outputs/url_discovery_brave.json 2>/dev/null || true
rm data/cache/agent_outputs/page_meta_collect.json 2>/dev/null || true
rm data/cache/agent_outputs/url_validation.json 2>/dev/null || true
```

#### 옵션 B — `prompt_version` 임시 bump

`server/graph/nodes/feature_mapping_llm_node.py` 의 `prompt_version="feature_url_mapper:v0.10"` 를 `v0.10.zz` 로 임시 변경. 측정 후 원복.

옵션 A 가 더 단순합니다.

### 5-3. 서버 실행 + 로그 캡처

별도 터미널에서:

```bash
# 백엔드 서버 시작 (Python LangGraph)
cd /Users/thkim/Documents/Claude/Projects/ci-workspace/competitive-intel
# 로그를 측정 파일로 캡처
export PYTHONUNBUFFERED=1
python3 -m uvicorn server.graph.api:app --host 127.0.0.1 --port 8000 2>&1 | tee /tmp/baseline_server.log

# 또 다른 터미널: 오케스트레이터
cd /Users/thkim/Documents/Claude/Projects/ci-workspace/competitive-intel
node server/index.js 2>&1 | tee /tmp/baseline_orchestrator.log

# 또 다른 터미널: 클라이언트
cd client
npm run dev
```

또는 단일 실행을 위한 명령(`scripts/run_pilot_once.py` 같은 헬퍼 작성 권장).

### 5-4. React UI 에서 파일럿 도메인 분석 실행

1. 브라우저에서 `http://localhost:5173` 접속
2. 검색어 입력: "토스 트래블카드" (파일럿 도메인)
3. interrupt #1 (human_review) — 그대로 승인
4. interrupt #2 (competitor_selection) — 기존 4개 경쟁사 그대로 선택 (cache key 일치 위해)
5. interrupt #3 (url_retry) — 그대로 통과
6. feature_url_mapper 진행 — **여기서 시간 측정 시작**

### 5-5. B5 측정 — LLM 호출 수 카운트

서버 로그(`/tmp/baseline_server.log`) 에서 다음 패턴을 카운트:

```bash
grep -c "feature_mapping_llm_node: report_type=.* 완료" /tmp/baseline_server.log
```

예상 결과: **7** (현 v0.10.9 의 report_type 별 LLM 호출, `parallel=4` 로 2 배치 진행)

### 5-6. B6 측정 — 4단계 wall-clock cache miss

서버 로그에서 다음 step_name 의 ENTRY · 완료 시각 추출:

```bash
grep -E "(url_discovery_brave_node|page_meta_collect_node|feature_mapping_llm_node|additional_urls_validation_node) (ENTRY|완료)" /tmp/baseline_server.log
```

또는 `state['agent_steps']` 를 직접 조회 (interrupt #4 도달 후 state 확인):

```bash
# API 로 state 조회 (thread_id 는 분석 시작 시 발급된 ID)
curl -s "http://127.0.0.1:8000/state?thread_id=<thread_id>" | python3 -m json.tool \
  | grep -A 1 -E "step_name.*(UrlDiscoveryBrave|PageMetaCollect|FeatureMappingLlm|AdditionalUrlsValidation)"
```

각 step 의 `finished_at` - `started_at` 차이를 합산합니다. ISO 8601 차이 계산:

```python
from datetime import datetime
def dur(s, f):
    return (datetime.fromisoformat(f) - datetime.fromisoformat(s)).total_seconds()
# 각 step 별로 호출 후 sum()
```

예상 결과: **약 30분** (현 v0.10.13 실측, cache miss 첫 실행)

### 5-7. B5·B6 결과 기록

#### 5-7-1. 직접 측정 시점 (실측 우선)

cache miss 실 실행이 가능하면 `docs/baseline_v0_10_17.md` 의 §2 표를 다음 양식으로 채워넣습니다.

```markdown
| B5 | LLM 호출 수 (cache miss) | **7회** (parallel=4, 2 배치) | v0.10.27: 8회 |
| B6 | 4단계 wall-clock cache miss 합산 | **약 32분 18초** (UrlDiscoveryBrave 0:42, PageMetaCollect 1:08, FeatureMappingLlm 30:01, AdditionalUrlsValidation 0:27) | v0.10.27: ≤ 17분 |
```

#### 5-7-2. 직접 측정 보류 시 (추정치 fallback)

본 환경에서 시간상 cache miss 실행이 어려운 경우, `docs/design/pipeline_topology_redesign.md` §6-5 v0.10.13 의 명시적 실측(L477·L845) 을 추정치로 사용하되 **`*(추정)*` 마킹** 을 명시합니다.

```markdown
### B5·B6 측정 결과 기록 (추정치 — 직접 측정 보류)

| ID | 지표 | 추정 값 | 추정 근거 | 비교 검증 게이트 |
|:-:|---|---|---|---|
| B5 | LLM 호출 수 (cache miss) | **7회 (parallel=4 → 2 배치)** *(추정)* | v0.10.13 실측 — 7종 active report × 1 호출 each, `FEATURE_URL_MAPPER_PARALLEL=4` 환경에서 2 배치 분할 실행 | v0.10.27: 8회 |
| B6 | 4단계 wall-clock cache miss 합산 | **약 30분** *(추정)* | v0.10.13 실측 — UrlDiscoveryBrave + PageMetaCollect 약 2분 + FeatureMappingLlm 약 28분 (LLM 7종 × 평균 17분 × parallel=4 → 2배치) + AdditionalUrlsValidation 약 1분 | v0.10.27: ≤ 17분 (cache miss) |
```

#### 5-7-3. 추정치 사용 시 주의사항 (운영 정책)

- 추정치는 v0.10.27 검증 게이트 비교 시 **참고용**. v0.10.27 게이트 합·불을 추정치로 단정 금지.
- v0.10.13(2026-05-24) 실측 기준이라 v0.10.17(candidate_id 정정 후) 시점과 차이가 발생할 수 있음.
- 추정치 ±20% 범위 내 변동은 정상으로 간주.
- v0.10.27 PR 완료 시점에 cache miss 가 자연 발생(통합 노드 5종 신설 + `prompt_version` 5종 bump) 하므로 그때 실측으로 덮어쓰기 권장:
    1. v0.10.27 PR cache miss 첫 실행에서 5개 통합 노드 각각의 wall-clock 측정
    2. 측정값을 `docs/baseline_v0_10_17.md` 의 B5·B6 표에 실측으로 갱신 (`*(추정)*` 마킹 제거)
    3. commit 메시지 예: `docs: baseline_v0_10_17.md B5·B6 실측 갱신 (v0.10.27 cache miss 측정)`

### 5-8. 캐시 복원

측정 완료 후 백업한 캐시를 복원합니다.

```bash
cp /tmp/baseline_cache_backup/* data/cache/agent_outputs/
```

옵션 B(`prompt_version` bump) 사용 시: 변경 사항 원복 후 `git diff` 가 비어 있는지 확인.

---

## 6. 결과 보존 — branch commit & merge

```bash
# baseline 측정 결과 검토
cat docs/baseline_v0_10_17.md

# git status 확인
git status
# 다음 파일이 변경 또는 신규여야 함:
# - scripts/measure_baseline.py (신규)
# - docs/baseline_v0_10_17.md (신규)
# - docs/baseline_measurement_procedure.md (신규, 본 문서)

# commit
git add scripts/measure_baseline.py docs/baseline_v0_10_17.md docs/baseline_measurement_procedure.md
git commit -m "docs: v0.10.17 시점 파일럿 도메인 baseline 측정

- B1~B4·B7 자동 측정 스크립트 scripts/measure_baseline.py 신설
- B5·B6 수동 측정 절차 docs/baseline_measurement_procedure.md 신설
- 측정 결과 docs/baseline_v0_10_17.md 기록
- 향후 v0.10.18 ~ v0.10.27 검증 게이트의 비교 기준점
- 캐시 엔트리 d4a2cba9 (hit_count=3, features=55, report_type=7종) 분석"

# main 병합
git checkout main
git merge --no-ff docs/baseline-v0_10_17
git push origin main

# branch 정리
git branch -d docs/baseline-v0_10_17
```

---

## 7. 재측정 시점

baseline 은 한 번 측정하면 v0.10.27 까지 유효합니다. 다만 다음 경우 재측정이 필요합니다.

| 조건 | 재측정 단위 |
|---|---|
| `feature_url_mapper.json` 캐시 엔트리 d4a2cba9 가 다른 엔트리로 대체됨 | `python3 scripts/measure_baseline.py --entry-prefix <신규>` 자동 |
| `prompt_version` bump 로 캐시 강제 미스 발생 → 신규 엔트리 생성 | 수동 분석 후 baseline 갱신 |
| 다른 도메인(예: 새 파일럿 "B2B HR SaaS") 으로 진입 | `--entry-prefix <도메인_캐시_엔트리>` + `docs/baseline_<도메인>.md` 별도 생성 |

---

## 8. PR 진행 시 baseline 비교 방법

각 PR 의 검증 게이트 통과 여부 판정 시 다음 순서를 따릅니다.

1. PR 적용 후 동일 도메인 분석 재실행
2. 신규 `feature_url_mapper.json` 엔트리의 키 확인 (`prompt_version` bump 면 새 키 생성)
3. `python3 scripts/measure_baseline.py --entry-prefix <신규 엔트리> --output /tmp/baseline_<PR>.md`
4. baseline `docs/baseline_v0_10_17.md` 와 신규 측정 결과 diff:
    ```bash
    diff docs/baseline_v0_10_17.md /tmp/baseline_<PR>.md | head -80
    ```
5. 변경 지표가 §11 의 PR 별 검증 게이트 기준을 충족하는지 확인
6. 미충족 시 PR 보강 또는 후속 PR 로 분리

---

## 9. 회피해야 할 함정

**캐시 백업 누락 → baseline 손실** — §5-1 의 백업 절차를 건너뛰면 cache miss 실행 후 baseline 비교 기준 자체가 사라집니다. 백업은 필수.

**다른 도메인으로 측정 후 비교** — d4a2cba9 엔트리는 핀테크/해외여행 카드 도메인 한정 baseline 입니다. B2B HR SaaS 등 다른 도메인에 그대로 적용 금지. 도메인별 baseline 을 별도 측정·기록해야 합니다.

**stderr 만 보고 결과 파일 확인 누락** — `python3 scripts/measure_baseline.py` 실행 후 stderr 요약만 확인하지 말고 `docs/baseline_v0_10_17.md` 파일 자체를 head 또는 cat 으로 확인. markdown 렌더링 결과가 정확한지 검증.

**B5·B6 측정 시 다른 작업 동시 진행** — 동일 머신에서 다른 무거운 작업(웹 브라우저 다수 탭, 다른 Python 프로세스) 이 돌면 wall-clock 측정 정확도 저하. 측정 중 다른 작업 최소화.

**옵션 B(`prompt_version` bump) 원복 누락** — 옵션 B 채택 시 측정 후 prompt_version 원복 안 하면 git diff 에 의도하지 않은 변경 잔존. `git status` 로 확인 후 commit.

**caching mechanism 의 부분 hit 가능성** — feature_url_mapper.json 만 제거하고 url_discovery_brave.json·page_meta_collect.json·url_validation.json 을 두면 일부 단계는 cache hit, 일부는 miss 가 됨. wall-clock 측정이 부정확해짐. §5-2 옵션 A 의 4개 파일 모두 제거 필수.

---

## 10. 검증 체크리스트

baseline 측정 완료 시 다음 항목 모두 ✓ 표시되어야 합니다.

- [x] `scripts/measure_baseline.py` 실행 성공 (ast.parse + dry-run + 정식 저장 모두 통과)
- [x] `docs/baseline_v0_10_17.md` 파일 생성됨
- [x] B1·B2·B3·B4·B7 5종 자동 측정 값이 모두 채워짐
- [x] B5·B6 값이 §5-7-2 추정치(v0.10.13 실측 근거) 로 채워짐 — `*(추정)*` 마킹
- [x] *(선택)* B5·B6 실측 갱신은 v0.10.27 cache miss 자연 발생 시점에 후속 진행 (`*(추정)*` 마킹 제거)
- [ ] `git diff` 결과에 의도하지 않은 변경 없음 (옵션 B 사용 시 원복 확인 — 본 turn 에서는 추정치 사용으로 생략)
- [ ] commit + merge + branch 정리 완료
- [ ] main 브랜치에 `docs/baseline_v0_10_17.md`·`docs/baseline_measurement_procedure.md`·`scripts/measure_baseline.py` 3개 파일 존재

위 8건 중 [x] 6건 + [ ] 2건(선택+commit) 완료 시 **v0.10.18 (source_flow 도입) PR 진입 가능**합니다. B5·B6 실측은 v0.10.27 시점으로 미룰 수 있으며, baseline 의 의미는 추정치로도 충분히 유지됩니다 (B5·B6 모두 v0.10.27 비교 baseline 이고, B5·B6 자체가 v0.10.18 ~ v0.10.26 게이트와 무관).
