'use strict';

/**
 * pythonServer.js
 * ----------------
 * Express 앱 기동 시 Python FastAPI(LangGraph) 서버를 subprocess로 시작하고
 * 종료 시 함께 정리하는 모듈.
 *
 * 아키텍처
 * --------
 *   Express 시작
 *     └─ startPythonServer()
 *          └─ child_process.spawn('python3', ['-m', 'uvicorn', ...])
 *               └─ server/graph/api.py 가 FastAPI + LangGraph 서버로 실행됨
 *                    └─ MemorySaver 체크포인터가 프로세스 메모리에 유지됨
 *                         → interrupt() + MemorySaver 가 정상 동작하는 이유
 *
 * Express ↔ Python 통신
 * ----------------------
 *   Express routes   →  fetch('http://127.0.0.1:PORT/invoke', ...)
 *   Python FastAPI   ←  HTTP JSON
 *
 * 사용 방법 (Express 진입점에서)
 * --------------------------------
 *   const { startPythonServer, PYTHON_SERVER_URL } = require('./pythonServer');
 *
 *   // 서버 시작 (기동 완료 대기 포함)
 *   await startPythonServer();
 *
 *   // 이후 라우트에서 사용
 *   const res = await fetch(`${PYTHON_SERVER_URL}/invoke`, { ... });
 */

const { spawn }      = require('node:child_process');
const { existsSync } = require('node:fs');
const http           = require('node:http');
const path           = require('node:path');

const PYTHON_SERVER_PORT = parseInt(process.env.PYTHON_SERVER_PORT || '8001', 10);
const PYTHON_SERVER_HOST = '127.0.0.1';

/** Express 라우트에서 사용할 Python 서버 기본 URL */
const PYTHON_SERVER_URL  = `http://${PYTHON_SERVER_HOST}:${PYTHON_SERVER_PORT}`;

/** 프로젝트 루트 경로 (이 파일 기준: server/ → 상위 폴더) */
const PROJECT_ROOT = path.resolve(__dirname, '..');

/**
 * 프로젝트 전용 가상 환경(.venv)이 있으면 그 Python을 사용한다.
 * 없으면 환경변수 PYTHON_BIN → 시스템 python3 순으로 폴백.
 *
 * conda 등 전역 환경의 패키지 버전 충돌(langchain.debug 등)을 방지하기 위해
 * 반드시 .venv를 먼저 사용하는 것이 권장된다.
 *   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
 */
const VENV_PYTHON = path.join(PROJECT_ROOT, '.venv', 'bin', 'python3');
const PYTHON_BIN  =
  existsSync(VENV_PYTHON)
    ? VENV_PYTHON
    : (process.env.PYTHON_BIN || 'python3');

/** 기동 완료 감지 타임아웃 (ms) */
const STARTUP_TIMEOUT_MS = 30_000;

/** /health 폴링 간격 (ms) */
const HEALTH_POLL_INTERVAL_MS = 500;

let pythonProcess = null;

/**
 * Python FastAPI 서버를 subprocess로 기동하고
 * /health 엔드포인트가 응답할 때까지 대기한다.
 *
 * @returns {Promise<void>} 서버 기동 완료 시 resolve
 * @throws {Error}          STARTUP_TIMEOUT_MS 초과 시 reject
 */
async function startPythonServer() {
  if (pythonProcess) {
    console.log('[pythonServer] 이미 실행 중입니다.');
    return;
  }

  console.log(`[pythonServer] Python FastAPI 서버 기동 시작 (port=${PYTHON_SERVER_PORT})`);
  console.log(`[pythonServer] Python 실행 경로: ${PYTHON_BIN}`);

  pythonProcess = spawn(
    PYTHON_BIN,
    [
      '-m', 'uvicorn',
      'server.graph.api:app',
      '--host', PYTHON_SERVER_HOST,
      '--port', String(PYTHON_SERVER_PORT),
      '--log-level', 'warning',   // uvicorn 자체 로그는 warning 이상만 출력
    ],
    {
      cwd:   PROJECT_ROOT,
      env:   { ...process.env },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );

  pythonProcess.stdout.on('data', (data) =>
    process.stdout.write(`[LangGraph] ${data}`)
  );
  pythonProcess.stderr.on('data', (data) =>
    process.stderr.write(`[LangGraph] ${data}`)
  );
  pythonProcess.on('error', (err) =>
    console.error('[pythonServer] 프로세스 오류:', err.message)
  );
  pythonProcess.on('exit', (code, signal) => {
    console.log(`[pythonServer] 프로세스 종료 (code=${code}, signal=${signal})`);
    pythonProcess = null;
  });

  // /health 가 응답할 때까지 폴링
  await waitForHealth();
  console.log('[pythonServer] 기동 완료.');
}

/**
 * Python 서버를 SIGTERM으로 종료한다.
 * Express 프로세스 종료 시 자동 호출된다.
 */
function stopPythonServer() {
  if (pythonProcess) {
    console.log('[pythonServer] Python 서버 종료 중…');
    pythonProcess.kill('SIGTERM');
    pythonProcess = null;
  }
}

// ── 프로세스 종료 훅 ──────────────────────────────────────────────────────────
process.on('exit',    stopPythonServer);
process.on('SIGINT',  () => { stopPythonServer(); process.exit(0); });
process.on('SIGTERM', () => { stopPythonServer(); process.exit(0); });

// ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

/**
 * /health 엔드포인트가 200을 반환할 때까지 폴링한다.
 *
 * @returns {Promise<void>}
 */
function waitForHealth() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + STARTUP_TIMEOUT_MS;

    const poll = () => {
      if (Date.now() > deadline) {
        reject(new Error(
          `[pythonServer] ${STARTUP_TIMEOUT_MS}ms 내에 기동되지 않았습니다.`
        ));
        return;
      }

      const req = http.get(
        `http://${PYTHON_SERVER_HOST}:${PYTHON_SERVER_PORT}/health`,
        (res) => {
          if (res.statusCode === 200) {
            resolve();
          } else {
            setTimeout(poll, HEALTH_POLL_INTERVAL_MS);
          }
        },
      );
      req.on('error', () => setTimeout(poll, HEALTH_POLL_INTERVAL_MS));
      req.end();
    };

    // 서버가 바인딩되기까지 짧게 대기 후 첫 폴링
    setTimeout(poll, HEALTH_POLL_INTERVAL_MS);
  });
}

module.exports = { startPythonServer, stopPythonServer, PYTHON_SERVER_URL };
