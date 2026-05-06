import { useState } from 'react';

/**
 * SearchPage
 * ----------
 * 사용자가 분석 검색어를 입력하는 첫 화면.
 * "분석 시작" 버튼 클릭 → /api/intake 호출 → 부모(App)에게 결과 전달
 */
export default function SearchPage({ onIntakeComplete }) {
  const [query, setQuery]     = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const res = await fetch('/api/intake', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ raw_query: query.trim() }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || `서버 오류 (HTTP ${res.status})`);
      }

      onIntakeComplete(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-xl">
        {/* 헤더 */}
        <div className="text-center mb-10">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">
            경쟁 분석 시작
          </h1>
          <p className="text-gray-500 text-sm">
            분석할 상품명이나 도메인을 입력하세요.
            <br />
            예: <span className="font-medium text-gray-700">토스 트래블카드</span>,{' '}
            <span className="font-medium text-gray-700">카카오페이</span>
          </p>
        </div>

        {/* 검색 폼 */}
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="예: 토스 트래블카드"
            disabled={loading}
            className="flex-1 px-4 py-3 rounded-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-gray-900 placeholder-gray-400 disabled:bg-gray-100 text-base"
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="px-6 py-3 bg-blue-600 text-white rounded-xl font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
          >
            {loading ? (
              <span className="flex items-center gap-2">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                </svg>
                분석 중…
              </span>
            ) : '분석 시작'}
          </button>
        </form>

        {/* 에러 메시지 */}
        {error && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
            {error}
          </div>
        )}

        <p className="mt-4 text-xs text-gray-400 text-center">
          AI가 검색어를 분석해 경쟁사 탐색용 초안을 자동 생성합니다.
          생성된 초안은 다음 단계에서 검토·수정할 수 있습니다.
        </p>
      </div>
    </div>
  );
}
