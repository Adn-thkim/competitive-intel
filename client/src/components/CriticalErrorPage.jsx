/**
 * CriticalErrorPage
 * -----------------
 * 파이프라인이 치명적 오류(critical_error)로 강제 종료되었을 때 표시하는 화면.
 *
 * 현재 발생 조건:
 *   - own_* 항목(자사 상품)의 URL이 Phase 2 재시도 후에도 validated=False
 *     → 자사 데이터 없는 경쟁 분석은 신뢰할 수 없으므로 분석을 중단.
 *
 * Props
 * -----
 * - message  : string — state.critical_error 메시지
 * - onReset  : () => void — 처음으로 돌아가기
 */
export default function CriticalErrorPage({ message, onReset }) {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4 py-12">
      <div className="max-w-lg w-full">

        {/* 아이콘 + 제목 */}
        <div className="flex flex-col items-center text-center mb-8">
          <div className="w-16 h-16 rounded-full bg-red-100 flex items-center justify-center mb-4">
            <span className="text-3xl">🚫</span>
          </div>
          <h1 className="text-2xl font-bold text-gray-900 mb-2">분석을 진행할 수 없습니다</h1>
          <p className="text-sm text-gray-500">
            신뢰할 수 없는 분석 결과는 분석을 하지 않는 것보다 치명적입니다.
          </p>
        </div>

        {/* 오류 메시지 박스 */}
        <div className="bg-red-50 border border-red-200 rounded-2xl p-5 mb-6">
          <div className="flex items-start gap-3">
            <span className="text-red-500 text-lg shrink-0 mt-0.5">⚠️</span>
            <div>
              <p className="text-sm font-semibold text-red-800 mb-1">중단 원인</p>
              <p className="text-sm text-red-700 leading-relaxed">{message}</p>
            </div>
          </div>
        </div>

        {/* 안내 */}
        <div className="bg-white border border-gray-200 rounded-2xl p-5 mb-6">
          <p className="text-sm font-semibold text-gray-700 mb-3">해결 방법</p>
          <ol className="space-y-2 text-sm text-gray-600">
            <li className="flex items-start gap-2">
              <span className="text-gray-400 shrink-0 font-mono">1.</span>
              <span>처음으로 돌아가서 분석을 다시 시작하세요.</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-gray-400 shrink-0 font-mono">2.</span>
              <span>
                자사 상품의 공식 URL을 직접 확인한 뒤 URL 재시도 화면에서 입력하세요.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-gray-400 shrink-0 font-mono">3.</span>
              <span>
                URL은 <code className="bg-gray-100 px-1 py-0.5 rounded text-xs font-mono">https://</code>로 시작하는 공식 상품 페이지여야 합니다.
              </span>
            </li>
          </ol>
        </div>

        {/* 버튼 */}
        <button
          onClick={onReset}
          className="w-full py-3 bg-gray-900 text-white text-sm font-semibold rounded-xl hover:bg-gray-700 transition-colors"
        >
          처음으로 돌아가기
        </button>

      </div>
    </div>
  );
}
