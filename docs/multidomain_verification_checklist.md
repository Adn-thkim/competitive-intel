# 복수 공식 도메인 — 재기동 후 종단 검증 체크리스트

대상: 토스 트래블카드(`own_토스트래블카드`) 재분석 1회 실행 직후.
전제: **파이썬 서버(uvicorn) 재기동 완료**(코드 반영) → 새 분석 실행 → `official_source_resolver` 통과.

---

## 1. `data/cache/official_sources.json` → `entries["own_토스트래블카드"]`

확인 명령:
```bash
python3 -c "import json;e=json.load(open('data/cache/official_sources.json'))['entries']['own_토스트래블카드'];import pprint;pprint.pprint({k:e.get(k) for k in ('primary_url','official_urls','validated','_validated_at')})"
```

| 항목 | 기대값(PASS) | 의미 |
|---|---|---|
| `official_urls` 키 | **존재함**(리스트) | 자동 마이그레이션이 작동해 새 스키마로 재해석됨 |
| `official_urls` 호스트 | 전부 `{toss.im, tossbank.com, tossinvest.com}` 안 | 양성 게이트(클러스터)가 정상 작동 |
| 제3자 호스트 | `card-gorilla.com`·`namu.wiki` 등 **0건** | 수집 차단(①②)이 작동 |
| `validated` | `true` | primary HTTP 검증 통과 |
| `_validated_at` | 이번 실행 시각으로 갱신 | 캐시 재사용이 아니라 실제 재해석됨 |

- **GOAL(이상적)**: `official_urls`에 `toss.im`과 `tossbank.com`이 **둘 다** 포함 → 복수 도메인 회수 성공.
- **MIN(허용)**: 최소 primary 1건 + 클러스터 내 호스트만. 만약 `toss.im` 단독이면 → 기능은 정상이나 **회수율 한계**(Brave 쿼리가 tossbank.com 페이지를 후보로 못 올린 경우). 이때는 쿼리 보강(도메인별 `site:` 보조 쿼리) 또는 known_domains 기반 보강을 검토.
- **FAIL**: `official_urls` 키가 없음(=마이그레이션/재해석 미작동, 서버 재기동/새 실행 여부 재확인) 또는 제3자 호스트 포함(=차단/게이트 점검).

## 2. `data/review/official_url_gate_review.json`

확인 명령:
```bash
[ -f data/review/official_url_gate_review.json ] && python3 -c "import json;r=json.load(open('data/review/official_url_gate_review.json'))['records'];print('records:',len(r));[print(x['candidate_id'],x['host'],'|',x['reason'][:30]) for x in r]" || echo "파일 없음(정상 가능)"
```

| 상황 | 해석 | 조치 |
|---|---|---|
| 파일 없음 | 게이트 탈락 0건 — 정상 | 없음 |
| `own_토스트래블카드` 레코드 **없음** | 토스 후보가 전부 클러스터 통과 — 정상 | 없음 |
| `own_토스트래블카드` 레코드 **있음** | LLM이 known_domains["토스"] 밖 도메인을 official로 제안 | host 확인: 진짜 토스 도메인이면 `known_domains.json`에 승격, 제3자면 `blocklist.json`에 추가 |
| 타 candidate 레코드 | 미등록 브랜드의 승격 후보 | 검토 후 known_domains 승격 |

핵심: **`own_토스트래블카드` 레코드에 제3자 host가 있으면 안 됨**(있으면 수집 차단이 새는지 점검).

## 3. (선택) 종단 — 비교 매트릭스 데이터

`official_content_collection` 단계까지 진행했다면, `data/collection/official_content_collection/<run>/feature_pool.json`에서 `own_토스트래블카드` 셀이 `not_found` 일색이 아니라 값(explicit/partial)을 가지는지 확인. tossbank.com 페이지가 게이트를 통과해 입력에 포함되면 채워집니다.

---

## 빠른 합격 판정
- [ ] `official_urls` 키 존재 + 호스트 전부 클러스터 내 + 제3자 0건
- [ ] (이상) `toss.im` + `tossbank.com` 둘 다 포함
- [ ] 검토 JSON에 `own_토스` 제3자 레코드 없음
- [ ] `_validated_at` 이번 실행 시각
