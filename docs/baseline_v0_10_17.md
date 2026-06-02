# v0.10.17 시점 파일럿 도메인 Baseline

> - 측정일: 2026-06-02 14:25:20
> - 도메인: 핀테크 / 해외여행 특화 카드 (domain_id=3, 추정)
> - 캐시 엔트리: `d4a2cba937f37cff...` (`data/cache/agent_outputs/feature_url_mapper.json`)
> - 캐시 생성: 2026-05-24T02:06:17
> - hit_count: 3
> - 분석 features: 55개
> - 측정 스크립트: `scripts/measure_baseline.py`
> - 측정 절차: `docs/baseline_measurement_procedure.md`

---

## 1. 정량 지표 (B1~B4, B7) — 자동 측정

| ID  | 지표                                                             | 값                                                                                                                                                                  | 비교 검증 게이트                                        |
| :-: | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------ |
| B1  | reaction\_insight comp_* not_found 비율                          | **81.0%** (17/21)                                                                                                                                                  | v0.10.23 < 50%                                   |
| B2  | reaction\_insight comp_* existing_urls 외부 host 비율              | **0.0%** (0/4)                                                                                                                                                     | v0.10.23 ≥ 75%                                   |
| B3  | reaction\_insight additional_urls validated=True 비율            | **0.0%** (0/42)                                                                                                                                                    | Phase 3 ≥ 30%                                    |
| B4  | reaction\_insight comp_* candidate_coverage 평균 existing_urls 수 | **0.19** (existing_sum=4, coverage_count=21)                                                                                                                       | v0.10.27 비교 baseline                             |
| B7  | report_type 별 features 분포                                      | `{'comparison_matrix': 10, 'reaction_insight': 7, 'marketing_social': 8, 'battlecard': 8, 'positioning_map': 6, 'market_context_swot': 8, 'executive_summary': 8}` | v0.10.18: positioning_map · executive_summary 0건 |

## 2. 외부 측정 필요 지표 (B5, B6)

B5·B6 은 cache miss 실행 시간을 측정해야 하므로 실제 파이프라인 1회 재실행이 필요합니다.

### B5 — LLM 호출 수 (cache miss 첫 실행)

1. 현재 `data/cache/agent_outputs/feature_url_mapper.json` 를 임시 위치로 백업
2. 또는 `prompt_version` 을 임시로 bump 하여 강제 미스 유도
3. React UI(`http://localhost:5173`)에서 파일럿 도메인 분석 재실행 또는 직접 `python3 -m server.graph.api ...`
4. 백엔드 로그(`server` stdout) 에서 다음 패턴 카운트:
    ```
    feature_mapping_llm_node: report_type=<rt> 완료 (features=N)
    ```
5. 카운트 결과(예상 7회, `parallel=4`) 를 본 문서의 B5 항목에 기록
6. 측정 후 백업 캐시 복원 또는 `prompt_version` 원복

### B6 — 4단계 wall-clock cache miss

1. B5 측정과 동시에 진행 (단일 실행에서 모두 측정 가능)
2. 분석 완료 후 `state['agent_steps']` 에서 다음 step_name 의 
   `started_at` ~ `finished_at` 차이를 합산:
    - `UrlDiscoveryBrave`
    - `PageMetaCollect`
    - `FeatureMappingLlm`
    - `AdditionalUrlsValidation`
3. 합산 값(예상 약 30분) 을 본 문서의 B6 항목에 기록

### B5·B6 측정 결과 기록 (추정치 — 직접 측정 보류)

본 환경에서 cache miss 실 실행 측정이 시간상 보류되어 다음 추정치를 사용합니다. 추정 근거는 `docs/design/pipeline_topology_redesign.md` §6-5 v0.10.13 의 명시적 실측 기록(L477·L845)입니다. 추후 cache miss 가 자연 발생하는 시점(예: v0.10.23 `prompt_version` bump 직후)에 실측으로 갱신합니다.

| ID | 지표 | 추정 값 | 추정 근거 | 비교 검증 게이트 |
|:-:|---|---|---|---|
| B5 | LLM 호출 수 (cache miss) | **7회 (parallel=4 → 2 배치)** *(추정)* | v0.10.13 실측 — 7종 active report × 1 호출 each, `FEATURE_URL_MAPPER_PARALLEL=4` 환경에서 2 배치 분할 실행 | v0.10.27: 8회 |
| B6 | 4단계 wall-clock cache miss 합산 | **약 30분** *(추정)* | v0.10.13 실측 — UrlDiscoveryBrave + PageMetaCollect 약 2분 + FeatureMappingLlm 약 28분 (LLM 7종 × 평균 17분 × parallel=4 → 2배치) + AdditionalUrlsValidation 약 1분 | v0.10.27: ≤ 17분 (cache miss) |

**추정치 정합성 주의사항**

- 본 추정치는 v0.10.13 시점(2026-05-24) 의 실측에 근거하며, v0.10.17 시점(2026-05-24 이후 candidate_id 정정) 의 실측과는 차이가 발생할 수 있습니다.
- 추정치는 v0.10.27 검증 게이트 비교 시 **참고용**으로 사용하되, v0.10.27 PR 완료 시점에 다음 절차로 실측 갱신 권장:
    1. v0.10.27 PR 의 cache miss 첫 실행에서 5개 통합 노드 각각의 wall-clock 측정 (자연 발생)
    2. 측정값을 본 문서의 B5·B6 표에 실측으로 덮어쓰기 (`*(추정)*` 마킹 제거)
- 본 추정치로 v0.10.27 게이트 합·불을 단정하지 말 것 — 추정치 ±20% 범위 내 변동은 정상으로 간주.

---

## 3. 측정 메타데이터

- `official_source_resolver.json` 에서 추출한 공식 host: 총 **8개**
- 본 host 집합에 포함되지 않은 도메인은 B2 의 외부 host 비율에 계상됨

<details><summary>공식 host 전체 목록</summary>

```
hanacard.co.kr
shinhancard.com
tossbank.com
travel-wallet.com
www.hanacard.co.kr
www.shinhancard.com
www.tossbank.com
www.travel-wallet.com
```

</details>

---

## 4. 향후 PR 별 비교 지점

| PR | B1 목표 | B2 목표 | B3 목표 | B4 비교 | B5 목표 | B6 목표 | B7 목표 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|---|
| v0.10.18 | — | — | — | — | — | — | positioning_map · executive_summary 0건 |
| v0.10.23 | < 50% | ≥ 75% | — | — | — | — | — |
| v0.10.25 | — | — | ≥ 30% | — | — | — | — |
| v0.10.27 | — | — | — | 증가 또는 유지 | 8회 | ≤ 17분 (cache miss) | — |

---

## 5. 재측정 방법

```bash
# 기본 캐시 + 기본 prefix
python3 scripts/measure_baseline.py

# 다른 엔트리 prefix
python3 scripts/measure_baseline.py --entry-prefix <prefix>

# stdout 만 (파일 저장 안 함)
python3 scripts/measure_baseline.py --dry-run
```
