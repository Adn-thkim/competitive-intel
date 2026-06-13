# v0.10.17 시점 파일럿 도메인 Baseline

> - 측정일: 2026-06-03 16:55:00
> - 도메인: 핀테크 / 해외여행 특화 카드 (domain_id=3, 추정)
> - 캐시 엔트리: `009e6716dbc206f2...` (`data/cache/agent_outputs/feature_url_mapper.json`)
> - 캐시 생성: 2026-06-03T07:35:19
> - hit_count: 0
> - 분석 features: 34개
> - 측정 스크립트: `scripts/measure_baseline.py`
> - 측정 절차: `docs/baseline_measurement_procedure.md`

---

## 1. 정량 지표 (B1~B4, B7) — 자동 측정

| ID | 지표 | 값 | 비교 검증 게이트 |
|:-:|---|---|---|
| B1 | reaction_insight comp_* not_found 비율 | **61.1%** (11/18) | v0.10.23 < 50% |
| B2 | reaction_insight comp_* existing_urls 외부 host 비율 | **90.9%** (10/11) | v0.10.23 ≥ 75% |
| B3 | reaction_insight additional_urls validated=True 비율 | **0.0%** (0/27) | Phase 3 ≥ 30% |
| B4 | reaction_insight comp_* candidate_coverage 평균 existing_urls 수 | **0.61** (existing_sum=11, coverage_count=18) | v0.10.27 비교 baseline |
| B7 | report_type 별 features 분포 | `{'comparison_matrix': 8, 'reaction_insight': 6, 'marketing_social': 6, 'battlecard': 7, 'market_context_swot': 7}` | v0.10.18: positioning_map · executive_summary 0건 |

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

### B5·B6 측정 결과 기록 (수동 갱신)

| ID | 지표 | 측정 값 | 비교 검증 게이트 |
|:-:|---|---|---|
| B5 | LLM 호출 수 (cache miss) | _측정 후 기입_ | v0.10.27: 8회 |
| B6 | 4단계 wall-clock cache miss 합산 | _측정 후 기입_ | v0.10.27: ≤ 17분 |

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
