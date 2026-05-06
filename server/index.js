'use strict';

/**
 * server/index.js
 * ----------------
 * Express 앱 진입점.
 *
 * 시작 순서
 * ---------
 *   1. Python FastAPI (LangGraph) 서버를 subprocess로 기동하고 /health 응답 대기
 *   2. Express HTTP 서버 listen 시작
 *
 * 환경 변수
 * ---------
 *   PORT              Express 포트 (기본값: 3000)
 *   PYTHON_SERVER_PORT Python FastAPI 포트 (기본값: 8001)
 *
 * 라우트 구조
 * -----------
 *   /api/intake   → POST: 검색어 intake, LangGraph 신규 시작
 *   /api/approve  → POST: 사용자 폼 승인, LangGraph interrupt 재개
 *   /health       → GET: Express 서버 기동 확인
 */

const express = require('express');
const cors    = require('cors');

const { startPythonServer } = require('./pythonServer');
const analysisRouter        = require('./routes/analysisRouter');

const PORT = parseInt(process.env.PORT || '3000', 10);

async function bootstrap() {
  // ── 1. Python FastAPI 서버 기동 ───────────────────────────────────────────
  console.log('[server] Python LangGraph 서버 기동 시작...');
  try {
    await startPythonServer();
  } catch (err) {
    console.error('[server] Python 서버 기동 실패:', err.message);
    process.exit(1);
  }

  // ── 2. Express 앱 구성 ────────────────────────────────────────────────────
  const app = express();

  app.use(cors({ origin: ['http://localhost:5173', 'http://127.0.0.1:5173'] }));
  app.use(express.json());

  // ── 라우트 ────────────────────────────────────────────────────────────────
  app.use('/api', analysisRouter);

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok', service: 'express' });
  });

  // ── 404 핸들러 ────────────────────────────────────────────────────────────
  app.use((_req, res) => {
    res.status(404).json({ error: '요청한 경로를 찾을 수 없습니다.' });
  });

  // ── 전역 에러 핸들러 ──────────────────────────────────────────────────────
  // eslint-disable-next-line no-unused-vars
  app.use((err, _req, res, _next) => {
    console.error('[server] 처리되지 않은 오류:', err);
    res.status(500).json({ error: '서버 내부 오류가 발생했습니다.' });
  });

  // ── 3. Express listen ────────────────────────────────────────────────────
  app.listen(PORT, '127.0.0.1', () => {
    console.log(`[server] Express 서버 실행 중 → http://127.0.0.1:${PORT}`);
  });
}

bootstrap();
