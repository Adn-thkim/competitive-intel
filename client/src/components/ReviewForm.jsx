import { useState, useCallback } from 'react';
import FieldInput, { REQUIRED_FIELDS } from './FieldInput';

/**
 * display_fields 배열을 섹션으로 그룹핑한다.
 * own_product.* → "자사 상품 정보" 섹션
 * 나머지 → "분석 범위 설정" 섹션
 */
function groupFields(displayFields) {
  const ownProduct = displayFields.filter(f => f.field_path.startsWith('own_product.'));
  const others     = displayFields.filter(f => !f.field_path.startsWith('own_product.'));
  return { ownProduct, others };
}

/**
 * display_fields → { field_path: value } 초기 상태 변환
 */
function buildInitialValues(displayFields) {
  return Object.fromEntries(
    displayFields.map(f => [f.field_path, f.value ?? (Array.isArray(f.value) ? [] : '')])
  );
}

/**
 * 폼 values → draft_competitor_discovery_input 구조로 변환
 * (own_product.* 키를 own_product 객체로 중첩)
 */
function buildFormData(values) {
  const result = {};
  for (const [path, val] of Object.entries(values)) {
    if (path.startsWith('own_product.')) {
      if (!result.own_product) result.own_product = {};
      result.own_product[path.replace('own_product.', '')] = val;
    } else {
      result[path] = val;
    }
  }
  return result;
}

/**
 * 필수 필드 검증 → { field_path: 에러 메시지 } 맵 반환
 */
function validate(values) {
  const errors = {};
  for (const path of REQUIRED_FIELDS) {
    const val = values[path];
    const isEmpty = Array.isArray(val) ? val.length === 0 : !String(val ?? '').trim();
    if (isEmpty) {
      const label = FIELD_LABELS[path] || path;
      errors[path] = `'${label}' 필드는 필수 입력 항목입니다. 내용을 입력한 후 다시 확인 버튼을 눌러주세요.`;
    }
  }
  return errors;
}

// 에러 메시지용 라벨 맵
const FIELD_LABELS = {
  domain_name:            '분석 도메인',
  'own_product.brand':    '브랜드명',
  'own_product.name':     '상품명',
  'own_product.category': '상품 카테고리',
  problem_statement:      '핵심 문제',
  target_user:            '핵심 사용자',
  core_value_props:       '핵심 가치 제안',
  geography:              '분석 기준 시장',
};

// 정정 해제 tooltip 용 — query_intake draft 필드 라벨
const OVERRIDE_FIELD_LABELS = {
  domain_name:          '분석 도메인',
  own_product:          '자사 상품',
  problem_statement:    '핵심 문제',
  target_user:          '핵심 사용자',
  core_value_props:     '핵심 가치 제안',
  known_keywords:       '알려진 키워드',
  usage_context:        '사용 맥락',
  geography:            '분석 기준 시장',
  business_constraints: '비즈니스 제약',
};

/** 섹션 렌더링 헬퍼 */
function Section({ title, fields, values, errors, onChange, loading }) {
  if (!fields.length) return null;
  return (
    <div className="mb-6">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
        {title}
      </h3>
      <div className="flex flex-col gap-4">
        {fields.map(field => (
          <FieldInput
            key={field.field_path}
            field={field}
            value={values[field.field_path] ?? (Array.isArray(field.value) ? [] : '')}
            onChange={val => onChange(field.field_path, val)}
            errorMsg={errors[field.field_path]}
            disabled={loading}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * ReviewForm
 * ----------
 * interrupt_value(query_intake_output)의 display_fields를 기반으로
 * 사용자 검토·수정 폼을 렌더링한다.
 *
 * Props:
 *   intakeResult  — /api/intake 응답 전체
 *   onApproved    — /api/approve 완료 시 결과(state)를 전달하는 콜백
 *   onReset       — 처음으로 돌아가기 콜백
 */
export default function ReviewForm({ intakeResult, onApproved, onReset }) {
  const interruptValue = intakeResult.interrupt_value;
  const displayFields  = interruptValue?.display_fields ?? [];
  const assumptions    = interruptValue?.assumptions ?? [];
  const uncertainFields = new Set(interruptValue?.uncertain_fields ?? []);
  // 분석 기준(taxonomy) 캐시 선택 — interrupt#1 payload 의 taxonomy_choice
  const taxonomyChoice  = interruptValue?.taxonomy_choice ?? { exists: false, latest_date: '' };

  const [values, setValues]   = useState(() => buildInitialValues(displayFields));
  const [errors, setErrors]   = useState({});
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState(null);
  // 저장된 데이터 있으면 기본=재사용(false), 없으면 신규 생성 강제(true)
  const [regenerate, setRegenerate] = useState(!taxonomyChoice.exists);
  // 사용자 정정 오버라이드 — interrupt#1 payload 의 override_fields
  const [overrideFields, setOverrideFields] = useState(interruptValue?.override_fields ?? []);
  const [clearing, setClearing] = useState(false);

  const overrideLabels = overrideFields.map(f => OVERRIDE_FIELD_LABELS[f] ?? f);

  async function handleClearOverrides() {
    if (!overrideFields.length || clearing) return;
    setClearing(true);
    setApiError(null);
    try {
      const res = await fetch('/api/overrides/clear', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ raw_query: interruptValue?.raw_query }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `서버 오류 (HTTP ${res.status})`);
      setOverrideFields(data.remaining_fields ?? []);
    } catch (err) {
      setApiError(err.message);
    } finally {
      setClearing(false);
    }
  }

  const handleChange = useCallback((path, val) => {
    setValues(prev => ({ ...prev, [path]: val }));
    // 값이 변경되면 해당 필드 에러 즉시 제거
    setErrors(prev => {
      if (!prev[path]) return prev;
      const next = { ...prev };
      delete next[path];
      return next;
    });
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();

    // 클라이언트 사이드 검증
    const newErrors = validate(values);
    if (Object.keys(newErrors).length > 0) {
      setErrors(newErrors);
      // 첫 번째 에러 필드로 스크롤
      const firstErrorKey = Object.keys(newErrors)[0];
      document.querySelector(`[data-field="${firstErrorKey}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }

    setLoading(true);
    setApiError(null);

    try {
      const res = await fetch('/api/approve', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          thread_id: intakeResult.thread_id,
          form_data: { ...buildFormData(values), force_taxonomy_refresh: regenerate },
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || `서버 오류 (HTTP ${res.status})`);
      }

      onApproved(data);
    } catch (err) {
      setApiError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const { ownProduct, others } = groupFields(displayFields);
  const errorCount = Object.keys(errors).length;

  return (
    <div className="min-h-screen bg-gray-50 py-10 px-4">
      <div className="max-w-2xl mx-auto">

        {/* 헤더 */}
        <div className="mb-6">
          <button
            onClick={onReset}
            className="text-sm text-gray-400 hover:text-gray-600 flex items-center gap-1 mb-4"
          >
            ← 검색으로 돌아가기
          </button>
          <h2 className="text-2xl font-bold text-gray-900">
            초안 검토 및 수정
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            원본 검색어:{' '}
            <span className="font-medium text-gray-700">
              {interruptValue?.raw_query}
            </span>
          </p>
        </div>

        {/* AI 가정 사항 박스 */}
        {assumptions.length > 0 && (
          <div className="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-xl">
            <p className="text-xs font-semibold text-blue-700 mb-2">AI 초안 생성 시 적용된 가정</p>
            <ul className="list-disc list-inside space-y-0.5">
              {assumptions.map((a, i) => (
                <li key={i} className="text-xs text-blue-700">{a}</li>
              ))}
            </ul>
          </div>
        )}

        {/* 검증 오류 요약 */}
        {errorCount > 0 && (
          <div className="mb-6 p-4 bg-red-50 border border-red-300 rounded-xl">
            <p className="text-sm font-semibold text-red-700 mb-1">
              필수 입력 항목 {errorCount}개를 확인해 주세요
            </p>
            <ul className="list-disc list-inside space-y-0.5">
              {Object.values(errors).map((msg, i) => (
                <li key={i} className="text-xs text-red-600">{msg}</li>
              ))}
            </ul>
          </div>
        )}

        {/* 폼 */}
        <form onSubmit={handleSubmit} noValidate>
          <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6 mb-4">

            {/* 자사 상품 정보 섹션 */}
            <div data-field="own_product.name">
              <Section
                title="자사 상품 정보"
                fields={ownProduct}
                values={values}
                errors={errors}
                onChange={handleChange}
                loading={loading}
              />
            </div>

            {/* 구분선 */}
            {ownProduct.length > 0 && others.length > 0 && (
              <hr className="my-4 border-gray-100" />
            )}

            {/* 분석 범위 설정 섹션 — data-field 속성으로 스크롤 앵커 제공 */}
            {others.map(field => (
              <div key={field.field_path} data-field={field.field_path}
                   className={field !== others[0] ? 'mt-4' : ''}>
                <FieldInput
                  field={field}
                  value={values[field.field_path] ?? (Array.isArray(field.value) ? [] : '')}
                  onChange={val => handleChange(field.field_path, val)}
                  errorMsg={errors[field.field_path]}
                  disabled={loading}
                />
              </div>
            ))}
          </div>

          {/* API 에러 */}
          {apiError && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
              {apiError}
            </div>
          )}

          {/* 필수 안내 */}
          <p className="text-xs text-gray-400 mb-4">
            <span className="text-red-500 font-bold">*</span> 표시 항목은 필수 입력 항목입니다.
          </p>

          {/* 분석 기준(taxonomy) 캐시 선택 — 확인 버튼과 필수 안내 문구 사이로 이동(크기 유지) */}
          <div className="flex-none w-40">
            <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
              분석 기준
            </label>
            <select
              value={regenerate ? 'regenerate' : 'reuse'}
              onChange={e => setRegenerate(e.target.value === 'regenerate')}
              disabled={!taxonomyChoice.exists || loading}
              className="w-full px-3 py-3 border border-gray-300 rounded-xl text-sm
                         bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500
                         disabled:bg-gray-50 disabled:text-gray-400"
            >
              {taxonomyChoice.exists && (
                <option value="reuse">{taxonomyChoice.latest_date} 생성본</option>
              )}
              <option value="regenerate">신규 생성</option>
            </select>
          </div>
          <p className="mt-2 mb-4 text-xs text-gray-400">
            {taxonomyChoice.exists
              ? '동일한 검색 입력으로 저장된 분석 기준이 있습니다. 재사용하면 더 빠르게 분석 결과를 확인할 수 있습니다.'
              : '저장된 데이터 없음 — 분석 기준을 새로 생성합니다.'}
          </p>

          {/* 정정 해제 버튼(옛 드롭다운 자리) + 제출 버튼 */}
          <div className="flex items-end gap-3">
            <div className="flex-none w-40 relative group">
              <button
                type="button"
                onClick={handleClearOverrides}
                disabled={!overrideFields.length || clearing || loading}
                className="w-full py-3.5 px-4 whitespace-nowrap border border-gray-300 text-gray-700 font-semibold rounded-xl hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm"
              >
                {clearing ? '해제 중…' : '정정 해제'}
              </button>
              {/* hover tooltip — 클릭 시 해제될 필드 안내 */}
              <div className="pointer-events-none absolute bottom-full left-0 mb-2 w-56 px-3 py-2 rounded-lg bg-gray-800 text-white text-xs leading-relaxed opacity-0 group-hover:opacity-100 transition-opacity z-10">
                {overrideFields.length
                  ? `클릭 시 해제될 정정 항목: ${overrideLabels.join(', ')}`
                  : '해제할 정정 항목이 없습니다.'}
              </div>
            </div>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 py-3.5 px-6 whitespace-nowrap bg-blue-600 text-white font-semibold rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-base"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                  </svg>
                  경쟁사 탐색 중…
                </span>
              ) : '확인 — 경쟁사 탐색 시작'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
