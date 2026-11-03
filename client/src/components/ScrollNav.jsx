/**
 * ScrollNav.jsx (2026-11)
 * 화면 우하단에 고정되어 스크롤을 따라다니는 '맨 위로 / 맨 아래로' 화살표 버튼.
 * - 페이지가 화면보다 길 때만 표시. 맨 위에서는 ↑, 맨 아래에서는 ↓ 를 흐리게 비활성.
 * - 결과 화면처럼 내용이 점진적으로 늘어나는 경우도 ResizeObserver 로 높이 변화를 따라간다.
 */
import { useEffect, useState } from 'react';

const EDGE = 40;   // 맨 위/아래로 간주하는 여유(px)

function Arrow({ up }) {
  return (
    <svg viewBox="0 0 20 20" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {up ? <path d="M5 12l5-5 5 5" /> : <path d="M5 8l5 5 5-5" />}
    </svg>
  );
}

export default function ScrollNav() {
  const [state, setState] = useState({ scrollable: false, atTop: true, atBottom: false });

  useEffect(() => {
    const update = () => {
      const doc = document.documentElement;
      const max = doc.scrollHeight - window.innerHeight;
      setState({
        scrollable: max > EDGE,
        atTop: window.scrollY <= EDGE,
        atBottom: window.scrollY >= max - EDGE,
      });
    };
    update();
    window.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    const ro = new ResizeObserver(update);
    ro.observe(document.body);
    return () => {
      window.removeEventListener('scroll', update);
      window.removeEventListener('resize', update);
      ro.disconnect();
    };
  }, []);

  if (!state.scrollable) return null;

  const btn = (disabled) => [
    'w-10 h-10 rounded-full bg-white border border-gray-200 shadow-md',
    'flex items-center justify-center text-gray-600 transition',
    disabled ? 'opacity-30 cursor-default' : 'hover:bg-gray-50 hover:text-indigo-600',
  ].join(' ');

  return (
    <div className="fixed bottom-6 right-6 z-40 flex flex-col gap-2">
      <button type="button" aria-label="맨 위로" title="맨 위로" disabled={state.atTop}
        className={btn(state.atTop)}
        onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
        <Arrow up />
      </button>
      <button type="button" aria-label="맨 아래로" title="맨 아래로" disabled={state.atBottom}
        className={btn(state.atBottom)}
        onClick={() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' })}>
        <Arrow />
      </button>
    </div>
  );
}
