"""외부 조사(이력·재무·시장) — 최종 인사이트 리포트의 외부 데이터 블록 공급.

queries.py  검색어 결정론 생성(후보·도메인 → 트랙별 쿼리)
collect.py  LLM 탐색(external_search agent: WebSearch·WebFetch) → 문서 묶음(doc_id) → 원문 대조
agent.py    추출 agent 호출(캐시 우선, 스키마 강제)
compute.py  출처 검증 · 단위 정규화 · 시계열/점유율/이력 집계 (LLM 미사용)
노드: server/graph/nodes/external_research_node.py
"""
