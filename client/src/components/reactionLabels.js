/**
 * reactionLabels — 반응 파이프라인 고정 enum(perspective·question_type)의 한글 라벨.
 *
 * 정본은 백엔드 server/graph/reaction/cluster.py 의 PERSPECTIVES / QUESTION_TYPES 다.
 * 이 둘은 **고정 분류(도메인 무관 일반 관점·질문 의도)** 라 LLM 동적 번역이 아니라 정적 매핑으로 충분하다.
 * sub_cluster·super_aspect 는 군집 시 LLM 이 이미 한글로 생성하므로(예: "ATM 현금인출") 그대로 두고,
 * 영어 폴백(uncategorized)만 한글로 바꾼다. (백엔드 enum 변경 시 이 파일도 함께 갱신.)
 */
export const PERSPECTIVE_KO = {
  cost: '비용(수수료)',
  reliability: '작동 안정성',
  convenience: '편의성',
  coverage: '적용 범위',
  exchange_rate: '환율',
  benefit: '혜택',
  overall: '종합 평가',
  other: '기타',
};

export const QUESTION_TYPE_KO = {
  feasibility: '가능 여부',
  method: '방법',
  cost: '비용',
  condition: '조건·자격',
  comparison: '비교',
  recommendation: '추천 요청',
  troubleshooting: '원인·해결',
  safety_anxiety: '안전·불안',
  other: '기타',
};

const SUB_FALLBACK_KO = { uncategorized: '기타' };

export const pspKo = (k) => PERSPECTIVE_KO[k] || k;
export const qtKo  = (k) => QUESTION_TYPE_KO[k] || k;
// super_aspect·sub_cluster 는 대부분 한글(LLM 생성) → 영어 폴백만 치환, 나머지는 원문 유지.
export const subKo = (k) => SUB_FALLBACK_KO[k] || k;
