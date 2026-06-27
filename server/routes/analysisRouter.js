'use strict';

/**
 * server/routes/analysisRouter.js
 * ---------------------------------
 * 분석 파이프라인 관련 Express 라우트.
 *
 * 엔드포인트
 * ----------
 * POST /api/intake
 *   사용자가 입력한 raw_query로 LangGraph 파이프라인을 시작한다.
 *   - 요청 바디: { raw_query: string }
 *   - 성공 응답 (interrupt 발생 시):
 *       {
 *         thread_id: string,           // 이후 /api/approve에서 사용
 *         is_interrupted: true,
 *         interrupt_value: object,     // query_intake_output — 사용자에게 보여줄 폼 데이터
 *         next_nodes: string[]
 *       }
 *   - 성공 응답 (interrupt 없이 완료 시):
 *       { thread_id, is_interrupted: false, state: object }
 *
 * POST /api/approve
 *   사용자가 폼을 검토·수정하거나 경쟁사를 선택한 뒤 완료 버튼을 누르면 호출된다.
 *   interrupt() 이후 그래프 실행을 재개한다.
 *   - 요청 바디: { thread_id: string, form_data?: object, resume?: object }
 *     form_data 또는 resume 중 하나를 사용한다. form_data가 우선한다.
 *     - interrupt #1 (폼 검토): form_data = draft_competitor_discovery_input
 *     - interrupt #2 (경쟁사 선택): resume = { selected_ids: string[] }
 *   - 성공 응답: { thread_id, is_interrupted: bool, state: object }
 *
 * GET /api/state/:threadId
 *   특정 thread의 현재 그래프 상태를 반환한다. (디버그·UI 폴링용)
 *
 * 에러 응답 형식
 * --------------
 *   { error: string, detail?: string }
 *   HTTP 400: 요청 파라미터 누락
 *   HTTP 429: Python 서버 할당량 초과 (API quota)
 *   HTTP 502: Python 서버 통신 불가
 *   HTTP 500: 그 외 서버 오류
 */

const { randomUUID } = require('node:crypto');
const express        = require('express');
const { Agent }      = require('undici');

const { PYTHON_SERVER_URL } = require('../pythonServer');

const router = express.Router();

// ── Python invoke 전용 long-timeout dispatcher (v0.10.11) ─────────────────────
//
// Node.js native fetch 는 undici 기반이며 기본 headersTimeout / bodyTimeout 이 300초(5분)다.
// 그러나 Python `/invoke` 는 LangGraph 의 동기 호출이므로 모든 노드가 끝나야 응답 헤더가
// 도착한다. feature_mapping_llm_node 가 단독으로 600초까지 timeout 을 가질 수 있고,
// 다른 일반 노드도 300초까지 가능하므로, Express fetch 가 300초 시점에 강제 끊겨
// `fetch failed` 가 발생한다. 본 dispatcher 는 invoke 의 가능한 최대 wall-clock 을
// 안전하게 흡수하는 long timeout 을 부여한다.
//
// PYTHON_INVOKE_TIMEOUT_MS (기본 30분):
//   - 모든 일반 LLM 노드 × CLI_TIMEOUT(300s)            ≈ 1500s
//   - feature_mapping_llm × FEATURE_MAPPING_LLM_TIMEOUT(600s) ≈ 600s
//   - 신규 수집·리포트 노드 추가 시 여유               + 마진
//   = 약 30분(1,800,000 ms) 기본값. 운영자가 env 로 별도 조정 가능.
//
// 본 dispatcher 는 `/invoke` 전용이며, `/progress` polling fetch 에는 적용되지 않는다.
const PYTHON_INVOKE_TIMEOUT_MS = parseInt(
  process.env.PYTHON_INVOKE_TIMEOUT_MS || '1800000', 10
);
const pythonInvokeAgent = new Agent({
  headersTimeout: PYTHON_INVOKE_TIMEOUT_MS,
  bodyTimeout:    PYTHON_INVOKE_TIMEOUT_MS,
  connect:        { timeout: 10000 },
});

// ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

/**
 * Python FastAPI 서버의 /invoke 엔드포인트를 호출한다.
 *
 * @param {object} body  InvokeRequest 바디
 * @returns {Promise<object>}  InvokeResponse
 * @throws {Error}  HTTP 오류 또는 네트워크 오류
 */
async function callInvoke(body) {
  let res;
  try {
    res = await fetch(`${PYTHON_SERVER_URL}/invoke`, {
      method:     'POST',
      headers:    { 'Content-Type': 'application/json' },
      body:       JSON.stringify(body),
      // v0.10.11 — Python invoke 의 긴 wall-clock 을 흡수하는 long timeout dispatcher.
      // 기본 fetch 의 300s headersTimeout 으로 인한 race(`fetch failed`) 차단.
      dispatcher: pythonInvokeAgent,
    });
  } catch (networkErr) {
    const err = new Error('Python LangGraph 서버에 연결할 수 없습니다.');
    err.statusCode = 502;
    err.detail     = networkErr.message;
    throw err;
  }

  if (!res.ok) {
    const text  = await res.text().catch(() => '');
    const err   = new Error(
      res.status === 403
        ? 'API 할당량이 초과되었습니다. 잠시 후 다시 시도해 주세요.'
        : `Python 서버 오류 (HTTP ${res.status})`
    );
    err.statusCode = res.status === 403 ? 429 : 500;
    err.detail     = text;
    throw err;
  }

  return res.json();
}

// ── POST /api/intake ──────────────────────────────────────────────────────────

router.post('/intake', async (req, res) => {
  const { raw_query } = req.body || {};

  if (!raw_query || typeof raw_query !== 'string' || !raw_query.trim()) {
    return res.status(400).json({ error: 'raw_query가 필요합니다.' });
  }

  const threadId = randomUUID();

  try {
    const result = await callInvoke({
      thread_id: threadId,
      raw_query:  raw_query.trim(),
    });

    return res.json(result);
  } catch (err) {
    console.error('[POST /api/intake] 오류:', err.message, err.detail || '');
    return res
      .status(err.statusCode || 500)
      .json({ error: err.message, detail: err.detail });
  }
});

// ── POST /api/approve ─────────────────────────────────────────────────────────

router.post('/approve', async (req, res) => {
  // form_data(interrupt #1 폼 검토) 또는 resume(interrupt #2 경쟁사 선택) 중 하나 필수
  // background=true(리포트 단계 = feature_selection 제출)면 Python 이 백그라운드 실행 후
  // 즉시 ack(status:"running") 반환 → 프런트가 /api/state 의 job_status 로 폴링(유실 방지).
  const { thread_id, form_data, resume, background } = req.body || {};
  const resumePayload = form_data ?? resume;

  if (!thread_id || typeof thread_id !== 'string') {
    return res.status(400).json({ error: 'thread_id가 필요합니다.' });
  }
  if (!resumePayload || typeof resumePayload !== 'object') {
    return res.status(400).json({ error: 'form_data 또는 resume 중 하나가 필요합니다.' });
  }

  try {
    const result = await callInvoke({
      thread_id,
      resume: resumePayload,
      background: background === true,
    });

    return res.json(result);
  } catch (err) {
    console.error('[POST /api/approve] 오류:', err.message, err.detail || '');
    return res
      .status(err.statusCode || 500)
      .json({ error: err.message, detail: err.detail });
  }
});

// ── POST /api/overrides/clear ─────────────────────────────────────────────────
// human_review 에서 저장된 query_intake 정정 오버라이드를 해제한다.
//   body: { raw_query: string, field?: string }  field 미지정 시 전체 해제.
router.post('/overrides/clear', async (req, res) => {
  const { raw_query, field } = req.body || {};
  if (!raw_query || typeof raw_query !== 'string') {
    return res.status(400).json({ error: 'raw_query가 필요합니다.' });
  }
  try {
    const r = await fetch(`${PYTHON_SERVER_URL}/overrides/clear`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ raw_query, field: field ?? null }),
    });
    if (!r.ok) {
      const text = await r.text().catch(() => '');
      return res.status(500).json({ error: 'Python 서버 오류', detail: text });
    }
    return res.json(await r.json());
  } catch (err) {
    return res.status(502).json({
      error: 'Python LangGraph 서버에 연결할 수 없습니다.', detail: err.message,
    });
  }
});

// ── GET /api/progress/:threadId ──────────────────────────────────────────────
// Python FastAPI /progress/{thread_id} 를 프록시한다.
// 프런트엔드가 1~2초 간격으로 폴링해 현재 파이프라인 단계를 표시한다.
// 응답: { thread_id, progress: { stage, message, detail, current, total } | null }

router.get('/progress/:threadId', async (req, res) => {
  const { threadId } = req.params;

  let pyRes;
  try {
    pyRes = await fetch(
      `${PYTHON_SERVER_URL}/progress/${encodeURIComponent(threadId)}`
    );
  } catch (networkErr) {
    // 폴링 오류는 프런트엔드가 무시하므로 빈 progress로 응답
    return res.json({ thread_id: threadId, progress: null });
  }

  if (!pyRes.ok) {
    return res.json({ thread_id: threadId, progress: null });
  }

  const data = await pyRes.json();
  return res.json(data);
});


// ── GET /api/state/:threadId ──────────────────────────────────────────────────

router.get('/state/:threadId', async (req, res) => {
  const { threadId } = req.params;

  let pyRes;
  try {
    pyRes = await fetch(`${PYTHON_SERVER_URL}/state/${encodeURIComponent(threadId)}`);
  } catch (networkErr) {
    return res.status(502).json({
      error:  'Python LangGraph 서버에 연결할 수 없습니다.',
      detail: networkErr.message,
    });
  }

  if (!pyRes.ok) {
    const text = await pyRes.text().catch(() => '');
    return res.status(pyRes.status === 404 ? 404 : 500).json({
      error:  `상태 조회 실패 (HTTP ${pyRes.status})`,
      detail: text,
    });
  }

  const data = await pyRes.json();
  return res.json(data);
});

module.exports = router;
