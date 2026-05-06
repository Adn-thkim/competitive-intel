import { useState } from 'react';

/**
 * 신뢰도(confidence)에 따른 배지 색상
 */
function ConfidenceBadge({ confidence }) {
  if (confidence == null) return null;

  const pct = Math.round(confidence * 100);
  let colorClass = 'bg-green-100 text-green-700';
  if (confidence < 0.55) colorClass = 'bg-red-100 text-red-600';
  else if (confidence < 0.8) colorClass = 'bg-yellow-100 text-yellow-700';

  return (
    <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${colorClass}`}>
      신뢰도 {pct}%
    </span>
  );
}

/**
 * 태그 입력 (배열 필드용)
 * Enter 또는 쉼표로 항목 추가, ✕ 클릭으로 제거
 */
function TagInput({ value = [], onChange, placeholder, disabled, hasError }) {
  const [inputVal, setInputVal] = useState('');

  function addTag(raw) {
    const tag = raw.trim();
    if (!tag || value.includes(tag)) return;
    onChange([...value, tag]);
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addTag(inputVal);
      setInputVal('');
    } else if (e.key === 'Backspace' && !inputVal && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  }

  function handleBlur() {
    if (inputVal.trim()) {
      addTag(inputVal);
      setInputVal('');
    }
  }

  function removeTag(idx) {
    onChange(value.filter((_, i) => i !== idx));
  }

  return (
    <div
      className={`min-h-[44px] px-3 py-2 rounded-lg border flex flex-wrap gap-1.5 items-center focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-transparent transition-colors
        ${hasError ? 'border-red-400 bg-red-50' : 'border-gray-300 bg-white'}
        ${disabled ? 'bg-gray-50 opacity-60' : ''}`}
    >
      {value.map((tag, i) => (
        <span
          key={i}
          className="flex items-center gap-1 bg-blue-100 text-blue-800 text-sm px-2 py-0.5 rounded-md"
        >
          {tag}
          {!disabled && (
            <button
              type="button"
              onClick={() => removeTag(i)}
              className="text-blue-500 hover:text-blue-800 leading-none"
            >
              ✕
            </button>
          )}
        </span>
      ))}
      {!disabled && (
        <input
          type="text"
          value={inputVal}
          onChange={e => setInputVal(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          placeholder={value.length === 0 ? placeholder : '항목 추가…'}
          className="flex-1 min-w-[120px] outline-none bg-transparent text-sm text-gray-800 placeholder-gray-400"
        />
      )}
    </div>
  );
}

/**
 * PLACEHOLDER 맵 — field_path 기준
 */
const PLACEHOLDERS = {
  domain_name:              '예: 해외 결제/환전 특화 카드',
  'own_product.brand':      '예: 토스',
  'own_product.name':       '예: 토스 트래블카드',
  'own_product.category':   '예: 여행 특화 카드 상품',
  'own_product.provider':   '예: 비바리퍼블리카',
  'own_product.official_brand_url': '예: https://toss.im/',
  problem_statement:        '예: 해외여행 시 환전과 결제를 간편하게 처리하고 싶다',
  target_user:              '예: 해외여행자 (Enter로 추가)',
  core_value_props:         '예: 환전 편의성, 수수료 절감 (Enter로 추가)',
  known_keywords:           '예: 트래블카드, 환율우대 (Enter로 추가)',
  usage_context:            '예: 여행 전 환전, 여행 중 결제 (Enter로 추가)',
  geography:                '예: 대한민국',
  business_constraints:     '예: 국내 서비스 우선 (Enter로 추가)',
};

/**
 * 필수 필드 목록
 */
export const REQUIRED_FIELDS = new Set([
  'domain_name',
  'own_product.brand',
  'own_product.name',
  'own_product.category',
  'problem_statement',
  'target_user',
  'core_value_props',
  'geography',
]);

/**
 * FieldInput
 * ----------
 * display_fields 항목 하나를 렌더링하는 단위 컴포넌트.
 * 문자열 → text input / textarea, 배열 → TagInput
 */
export default function FieldInput({ field, value, onChange, errorMsg, disabled }) {
  const isRequired = REQUIRED_FIELDS.has(field.field_path);
  const isArray    = Array.isArray(value);
  const isLong     = field.field_path === 'problem_statement';
  const placeholder = PLACEHOLDERS[field.field_path] || '값을 입력하세요';
  const hasError   = Boolean(errorMsg);

  return (
    <div className="flex flex-col gap-1.5">
      {/* 라벨 행 */}
      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-sm font-medium text-gray-800">
          {field.label}
          {isRequired && (
            <span className="ml-1 text-red-500 font-bold" aria-label="필수">*</span>
          )}
        </label>
        <ConfidenceBadge confidence={field.confidence} />
        {!isRequired && (
          <span className="text-xs text-gray-400">(선택)</span>
        )}
      </div>

      {/* 추론 근거 */}
      {field.reason && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
          ⚠ {field.reason}
        </p>
      )}

      {/* 입력 위젯 */}
      {isArray ? (
        <TagInput
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          disabled={disabled}
          hasError={hasError}
        />
      ) : isLong ? (
        <textarea
          rows={3}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className={`px-3 py-2 rounded-lg border text-sm text-gray-900 placeholder-gray-400
            focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-y
            ${hasError ? 'border-red-400 bg-red-50' : 'border-gray-300 bg-white'}
            ${disabled ? 'bg-gray-50 opacity-60' : ''}`}
        />
      ) : (
        <input
          type="text"
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className={`px-3 py-2 rounded-lg border text-sm text-gray-900 placeholder-gray-400
            focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
            ${hasError ? 'border-red-400 bg-red-50' : 'border-gray-300 bg-white'}
            ${disabled ? 'bg-gray-50 opacity-60' : ''}`}
        />
      )}

      {/* 필수 필드 에러 메시지 */}
      {hasError && (
        <p className="text-xs text-red-600">
          {errorMsg}
        </p>
      )}
    </div>
  );
}
