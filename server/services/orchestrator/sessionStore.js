/**
 * sessionStore.js
 * ---------------
 * /api/intake → /api/approve 두 요청 사이에 run_id와 QueryIntakeAgent
 * 출력을 임시 보관하는 인메모리 세션 스토어.
 *
 * 선택 근거 (로컬 개발 단계)
 * --------------------------
 * - 외부 의존성 없음. Redis, DB 설정 없이 즉시 사용 가능.
 * - 단일 프로세스 로컬 앱에서 충분한 격리성 제공.
 * - 서버 재시작 시 세션이 초기화되는 것은 개발 단계에서 허용 범위.
 *
 * 프로덕션 전환 시 교체 경로
 * ---------------------------
 * LangGraph interrupt() + SqliteSaver 패턴을 도입하면
 * 이 스토어 자체가 불필요해진다.
 * (LangGraph 체크포인터가 run_id → 전체 그래프 상태를 직접 관리)
 *
 * 만약 interrupt() 없이 스케일아웃이 필요한 경우:
 *   create(runId, data) / get(runId) 인터페이스를 유지하면서
 *   내부 구현만 Redis client로 교체하면 된다.
 *
 * @module sessionStore
 */

'use strict';

/** 세션 기본 유효 기간 (밀리초). 기본 30분. */
const DEFAULT_TTL_MS = 30 * 60 * 1000;

/** 만료 세션 정리 주기 (밀리초). 기본 5분. */
const DEFAULT_CLEANUP_INTERVAL_MS = 5 * 60 * 1000;

/**
 * @typedef {Object} IntakeSession
 * @property {string}  run_id              - 파이프라인 실행 식별자
 * @property {string}  request_id          - 요청 추적 ID
 * @property {string}  raw_query           - 사용자 원문 검색어
 * @property {Object}  query_intake_output - QueryIntakeAgent 출력 전체
 * @property {number}  created_at_ms       - 생성 시각 (Date.now())
 * @property {number}  expires_at_ms       - 만료 시각 (Date.now() + TTL)
 */

class SessionStore {
  /**
   * @param {Object} [options]
   * @param {number} [options.ttlMs=1800000]           세션 유효 기간 (ms)
   * @param {number} [options.cleanupIntervalMs=300000] 만료 정리 주기 (ms)
   */
  constructor(options = {}) {
    /** @type {Map<string, IntakeSession>} */
    this._store = new Map();

    this._ttlMs = options.ttlMs ?? DEFAULT_TTL_MS;

    // 주기적으로 만료 세션을 정리한다.
    // unref()로 타이머가 프로세스 종료를 막지 않도록 한다.
    this._cleanupTimer = setInterval(
      () => this._cleanup(),
      options.cleanupIntervalMs ?? DEFAULT_CLEANUP_INTERVAL_MS
    ).unref();
  }

  // ── 공개 API ─────────────────────────────────────────────────────────────

  /**
   * 세션을 생성하고 저장한다.
   *
   * @param {string} runId              - /api/intake에서 생성한 run_id
   * @param {Object} data               - 저장할 세션 데이터
   * @param {string} data.request_id
   * @param {string} data.raw_query
   * @param {Object} data.query_intake_output
   * @returns {IntakeSession}           저장된 세션 객체
   * @throws {Error}                    runId가 비어 있거나 data가 없을 경우
   */
  create(runId, data) {
    if (!runId) throw new Error('runId는 비어 있을 수 없습니다.');
    if (!data?.query_intake_output) {
      throw new Error('data.query_intake_output이 필요합니다.');
    }

    const now = Date.now();
    /** @type {IntakeSession} */
    const session = {
      run_id:              runId,
      request_id:          data.request_id ?? '',
      raw_query:           data.raw_query ?? '',
      query_intake_output: data.query_intake_output,
      created_at_ms:       now,
      expires_at_ms:       now + this._ttlMs,
    };

    this._store.set(runId, session);
    return session;
  }

  /**
   * run_id로 세션을 조회한다.
   *
   * @param {string} runId
   * @returns {IntakeSession | null} 세션이 없거나 만료됐으면 null
   */
  get(runId) {
    const session = this._store.get(runId);
    if (!session) return null;

    if (Date.now() > session.expires_at_ms) {
      this._store.delete(runId);
      return null;
    }

    return session;
  }

  /**
   * 세션을 명시적으로 삭제한다.
   * /api/approve 완료 후 호출해 메모리를 즉시 해제한다.
   *
   * @param {string} runId
   */
  delete(runId) {
    this._store.delete(runId);
  }

  /**
   * 현재 스토어 상태를 반환한다. 디버깅·헬스체크 용도.
   *
   * @returns {{ total: number, active: number, expired: number }}
   */
  stats() {
    const now = Date.now();
    let expired = 0;

    for (const session of this._store.values()) {
      if (now > session.expires_at_ms) expired += 1;
    }

    return {
      total:   this._store.size,
      active:  this._store.size - expired,
      expired,
    };
  }

  /**
   * 타이머를 정리하고 모든 세션을 삭제한다.
   * 테스트 환경에서 명시적으로 호출해 타이머 누수를 방지한다.
   */
  destroy() {
    clearInterval(this._cleanupTimer);
    this._store.clear();
  }

  // ── 내부 메서드 ──────────────────────────────────────────────────────────

  /** 만료된 세션을 일괄 삭제한다. */
  _cleanup() {
    const now = Date.now();
    for (const [runId, session] of this._store.entries()) {
      if (now > session.expires_at_ms) {
        this._store.delete(runId);
      }
    }
  }
}

// 싱글턴: Express 앱 전체에서 하나의 스토어를 공유한다.
const sessionStore = new SessionStore();

module.exports = { SessionStore, sessionStore };
