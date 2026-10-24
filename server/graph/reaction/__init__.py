"""server/graph/reaction/ — 반응 파이프라인 코어 (통합계획 §1-C·§6, Phase 1).

scripts/poc_*·test_* 에서 검증한 코어 로직을 프로덕션 정본으로 이전한다. 프로덕션 노드
(reaction_*_node)는 **이 패키지만** 참조하고 scripts 를 import 하지 않는다. dev 스크립트는
반대로 이 패키지를 재-import 하여 재현성을 유지한다(중복 정의 제거).

모듈:
- llm      : LLM 호출(_cli)·병렬(pmap)·모델 상수·세션한도 조기중단  [Phase 1a — 이전 완료]
- gate     : 영상/도메인 게이트                                     [Phase 1b — 예정]
- extract  : 의견 units + 질문 questions 추출(제목 멤버·dedup)       [Phase 1c — 예정]
- cluster  : super_aspect 상향식 군집(Phase A/B)                     [Phase 1d — 예정]
"""
