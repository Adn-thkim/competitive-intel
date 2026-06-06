# comparison_matrix 노드 설계 — feature_pool 소비 → 비교 매트릭스 리포트

> - **상태**: DRAFT — 사용자 검토 후 확정 예정
> - **작성일**: 2026-06-05
> - **시리즈**: report generation 시리즈 2단계 (official_content_collection → comparison_matrix + graph 배선)
> - **선행 문서**:
>   - `docs/design/feature_extraction_node_design.md` (§6-1 feature_pool 계약 · FE-D11·FE-D12)
>   - `docs/reference/report_taxonomy.md` §2-1 (평가 루브릭 1–5점) · §4 (AP-1~AP-7)
>   - `docs/reference_comparison_matrix.md` (방법론 — Gartner CC·Harvey Balls·Weighted Scoring)
> - **대상 파일**: `server/graph/nodes/comparison_matrix_node.py` (스켈레톤 → 구현),
>   `agents/comparison_matrix/*` (system_prompt·output.schema 신규), `server/graph/graph.py` (배선)

---

## 1. 문서 목적과 범위

`official_content_collection`이 산출한 `feature_pool`(feature × candidate 2단계 키,
누락 셀 0건)을 소비하여 **비교 매트릭스 리포트**(`report_outputs["comparison_matrix"]`)를
생성하는 노드의 설계를 확정합니다. graph.py의 §2-3 edge 3개 활성화를 포함합니다
(스켈레톤 failed 노출 방지를 위해 구현과 배선을 한 PR로 묶음 — 사용자 확정 2026-06-05).

---

## 2. 핵심 설계 — 코드/LLM 책임 분리 (CM-D1)

official_content_collection의 "Step 3는 LLM 없이"와 동일 사상을 적용합니다.
**판단할 것이 없는 부분은 코드로, 정성 판정·서술만 LLM으로** 분리합니다.

| 책임 | 수행 주체 | 산출물 |
|---|---|---|
| feature_table 구성 (행=candidate, 열=feature, 셀=feature_pool 그대로) | **코드** (결정론) | `content.feature_table` |
| 표기 규칙 적용 (미확인·⚠수동검토·[기간한정] footnote) | **코드** | `content.promotional_footnotes` 등 |
| AP 함정 자동 경고 (AP-1·AP-2·AP-3 후보 탐지) | **코드** | `warnings` (일부) |
| source_references 집계 (셀 출처 URL × feature_id) | **코드** | envelope `source_references` |
| Winning/Battling/Losing Zone 판정 (own 관점) | **LLM** (1회 호출) | `content.zone_summary` |
| 정성 feature의 Harvey Balls 5단계 판정 | **LLM** | `content.harvey_balls` |
| 루브릭 자체 평가 (1–5점 + 사유) | **LLM** | envelope `evaluation_score` |

근거: 표 데이터는 feature_pool에 이미 확정된 사실이므로 LLM 재작성은 변동성·왜곡
위험만 추가합니다. LLM은 코드가 만든 표를 **읽기 전용 입력**으로 받아 판정만 수행하며,
수치를 재생성하지 않습니다.

---

## 3. 입출력 계약

### 3-1. read keys

| state 키 | 용도 |
|---|---|
| `feature_pool` | 매트릭스 셀 원천 (§6-1 계약 — status·evidence·is_promotional 포함) |
| `product_profiles` | candidate 표시명·needs_manual_review·profile_summary |
| `domain_taxonomy.report_config["comparison_matrix"]` | active 게이트 · label · categories · feature_labels · (옵션) action_lens |
| `selected_feature_ids` / `selected_competitor_ids` | 매트릭스 행·열 범위 |
| `own_product.product_id` | own 행 식별 (Zone 판정 기준점) |

### 3-2. write keys

- `report_outputs["comparison_matrix"]` — `build_report_envelope()` 표준 envelope.
- `content` 구조 (스켈레톤 docstring + Rubric §2-1 정합):

```python
content = {
  "title": str,                      # report_config.label 기반
  "feature_table": {
    "columns": [{"feature_id", "label", "category"}],     # 선택 feature, taxonomy 순서
    "rows": [{                                            # own 첫 행, comp_* 정렬
      "candidate_id", "candidate_name", "is_own": bool,
      "cells": {feature_id: {
        "display": str,              # 표기 규칙 적용된 표시값 (§4)
        "value", "value_numeric", "unit", "as_of",
        "extraction_status", "confidence", "source_url",
        "is_promotional", "valid_until",
        "manual_check_required": bool,                    # ⚠ 셀
        "footnote_refs": [int],                           # 각주 번호
      }},
    }],
  },
  "zone_summary": {                  # LLM — battlecard 흐름 B 인용 대상
    "winning":  [{"feature_id", "rationale"}],
    "battling": [{"feature_id", "rationale"}],
    "losing":   [{"feature_id", "rationale"}],
    "overall_comment": str,
  },
  "harvey_balls": [                  # LLM — 정성 feature 한정 (value_numeric 없는 열)
    {"feature_id", "legend": str, "ratings": {candidate_id: 0~4}},
  ],
  "use_case_weights": [...],         # action_lens 존재 시에만 LLM 산출 (없으면 빈 배열)
  "promotional_footnotes": [         # 코드 — AP-1 의무 기록
    {"ref": int, "candidate_id", "feature_id", "valid_until", "note"},
  ],
  "traps_footnote": [str],           # 코드 — AP-2·AP-3 후보 자동 명시 (Rubric 5점 요건)
}
```

---

## 4. 셀 표기 규칙 (CM-D2 — feature_pool 상태값 → 표시값)

| feature_pool 셀 상태 | display 규칙 | 근거 |
|---|---|---|
| `not_found` / `unknown` | **"미확인"** — 빈 값·열위로 표기 금지 | AP 함정 방지 (§6-1 계약) |
| `requires_manual_check` | value + `manual_check_required: true` (UI ⚠ 배지) | 충돌 셀은 단정 금지 |
| `is_promotional: true` | value + footnote 번호 부여, `promotional_footnotes`에 `valid_until` 기록 | AP-1 의무 기록 (FE-D12 소비) |
| `partial` + 절대 단어("무료"·"0%"·"면제") | `traps_footnote`에 AP-2 후보 자동 추가 | Rubric 3점 요건 |
| 정량(`value_numeric` 존재) + `as_of: ""` | `traps_footnote`에 AP-3 후보(시점 미표기) 자동 추가 | Rubric §2-1 |

LLM에도 동일 규칙을 프롬프트로 전달: 미확인 셀은 losing 판정 금지(판정 보류 또는
battling), ⚠ 셀은 Zone 판정에서 제외하고 사유 명시, 기간한정 셀은 영구 강점으로
취급 금지 (AP-1).

---

## 5. LLM 호출 설계

- **어댑터**: `ClaudeCodeCliAnalyzer` (기본 경로 원칙 — 결정론 요건 노드 아님.
  turn-49 일관 패턴, Future_Improvements 2번 정합). 테스트는 fake 주입.
- **호출 단위**: 리포트당 **1회**. 입력 = 코드가 구성한 feature_table(전체 셀) +
  own candidate_id + categories + (옵션) action_lens.
- **출력 스키마**: `agents/comparison_matrix/output.schema.json` —
  `zone_summary`·`harvey_balls`·`use_case_weights`·`evaluation_score`(1–5)·
  `score_rationale`·`warnings`. `additionalProperties: false`.
- **검증 가드 (코드, LLM 출력 후처리)**: zone·harvey의 feature_id가 columns에 없는
  항목 제거, ratings의 candidate_id가 rows에 없는 항목 제거 — LLM 환각 ID 차단.
- **캐시**: `agent_id="comparison_matrix"`. cache_input = feature_pool 해시 +
  selected ids + own_product_id. context = system_prompt + schema + 모델
  (feature_pool 변경 시 자동 cache miss).

### LLM 실패 시 degrade (§7 부분 실패 정책 정합)

LLM 호출 실패(어댑터 내부 재시도 소진) 시 노드를 fail시키지 않고 **코드 산출물만으로
envelope을 구성**합니다: zone_summary·harvey_balls 빈 값, evaluation_score는 코드가
루브릭 결정론 부분으로 산정(수치+단위+출처 충족 시 3점, 미충족 2점), warnings에
"LLM 판정 생략(degraded)" 기록 + errors 누적. 근거: 표 자체가 리포트 가치의
대부분이며, comparison_matrix 완전 실패는 흐름 B(positioning_map·battlecard) 전체를
차단하므로 표만이라도 전달하는 것이 §7 사상에 맞습니다.

---

## 6. graph.py 배선 (§2-3 — 사용자 확정: 구현과 동시)

```python
builder.add_node("official_content_collection", official_content_collection_node)
builder.add_node("comparison_matrix",           comparison_matrix_node)

builder.add_edge("feature_selection",           "official_content_collection")
builder.add_edge("official_content_collection", "comparison_matrix")
builder.add_edge("comparison_matrix",           END)   # 임시 — 후속 시리즈에서 교체
```

기존 `feature_selection → END` 임시 edge 제거. 모듈 하단 토폴로지 자가 진단 블록에
신규 edge 3종 검증 추가.

`report_outputs` write 충돌 주의 (CM-D3): 현 시리즈는 리포트 노드가 1개라 기본
replace로 안전하나, 후속 시리즈에서 복수 리포트 노드가 병렬 실행되면 동일 키 동시
write로 LangGraph InvalidUpdateError가 발생한다 — **dict merge reducer 도입을 후속
시리즈 선행 작업으로 기록**한다.

---

## 7. 검증 계획 (목표 주도형)

1. **단위 — 표 구성**: feature_pool fixture(미확인·⚠·기간한정 혼합) → 검증: 표기
   규칙 5종(§4) 정확 적용, own 행 첫 번째, footnote 번호 부여.
2. **단위 — AP 자동 경고**: AP-2(절대 단어+partial)·AP-3(정량+as_of 없음) 후보가
   traps_footnote에 포함.
3. **단위 — LLM 출력 가드**: 환각 feature_id·candidate_id가 후처리에서 제거.
4. **단위 — 캐시**: 동일 feature_pool 2회 → 2회차 LLM 호출 0건.
5. **단위 — degrade**: LLM 예외 시 표 전용 envelope + score 결정론 산정 + warnings.
6. **단위 — skip**: report_config active=false → make_skip_result.
7. **통합 — 토폴로지**: build_graph() 컴파일 + 신규 edge 3종 존재 + 기존
   `feature_selection → END` 부재 확인.

## 8. 결정 항목

| ID | 결정 | 상태 |
|---|---|---|
| CM-D1 | 코드/LLM 책임 분리 — 표·표기·집계·AP 자동 경고 = 코드, Zone·Harvey·점수 = LLM 1회 | **확정** (2026-06-05, 사용자 합의) |
| CM-D2 | 셀 표기 규칙 — 미확인(열위 단정 금지)·⚠ 수동검토·[기간한정] footnote (AP-1) | **확정** (2026-06-05) |
| CM-D3 | report_outputs merge reducer — 현 시리즈 불필요(단일 리포트), 복수 리포트 병렬화 전 도입 | 기록 — 후속 시리즈 선행 작업 |
| CM-D4 | LLM 어댑터 = ClaudeCodeCliAnalyzer (기본 경로 원칙 — 결정론 요건 아님) | 제안 |
| CM-D5 | LLM 실패 시 degrade 모드 (표 전용 envelope) — 흐름 B 차단 방지 | 제안 |
| CM-D6 | **루브릭 점수 = 코드 결정론 채점** (`_compute_rubric_score`) — LLM 자기평가 폐기. 배경: 프롬프트 골격 예시의 값 앵커링으로 자기평가 점수가 4→3 표류한 사고(2026-06-06). 규칙: 2점(정량 셀 단위·출처 누락) / 3점(전부 충족) / 4점(+use_case_weights 비어있지 않음) / 5점(+함정 각주 명시 + 정량 셀 as_of 전부 표기). LLM 출력에서 evaluation_score·score_rationale 제거 — 점수는 envelope 내용물의 함수로만 결정되어 프롬프트 변화에 불변 | **확정** (2026-06-06) |
