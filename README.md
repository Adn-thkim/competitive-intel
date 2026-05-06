# YouTube 경쟁 분석 도구 — Local 버전

YouTube 댓글 반응 데이터를 수집하여 자사/경쟁사 비교 분석을 수행하고,
인사이트 리포트(Excel/PDF)를 생성하는 로컬 전용 분석 도구입니다.

> **이 저장소는 Mac 로컬 환경 전용입니다. 외부 배포를 하지 않습니다.**
> 웹 앱 배포 버전은 [`youtube-reaction-app`](../youtube-reaction-app) 저장소를 참고하세요.

---

## 이 도구가 하는 일

```
검색어 입력 (자사/경쟁사 구분)
    ↓
YouTube 영상 9개 + 댓글 수집 (품질 기반 150~200개 선별)
    ↓
Claude API 구조화 분석 → SQLite 영구 저장
    ↓
복수 세션 집계 비교 (자사 강점 vs 경쟁사 강점)
    ↓
인사이트 리포트 생성 → Excel / PDF 출력
```

---

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| Frontend | React 18 + Vite + Tailwind CSS |
| Backend | Express.js (localhost:4000) |
| 데이터베이스 | SQLite (`better-sqlite3`) |
| 쿼리 빌더 | Knex.js |
| 분석 엔진 | Claude API (`@anthropic-ai/sdk`) |
| 데이터 수집 | YouTube Data API v3 |
| 리포트 출력 | SheetJS (Excel), pdfkit (PDF) |

---

## 시작하기

### 사전 요구사항

```bash
# Xcode Command Line Tools 설치 (better-sqlite3 빌드 필요)
xcode-select --install

# Node.js v18 이상 확인
node --version
```

### 1. 저장소 클론 및 의존성 설치

```bash
git clone https://github.com/[계정]/competitive-intel.git
cd competitive-intel

# 서버 의존성
npm install express better-sqlite3 cors knex dotenv @anthropic-ai/sdk axios xlsx pdfkit
npm install -D nodemon concurrently

# 클라이언트 의존성
cd client
npm install axios lucide-react
npm install -D vite @vitejs/plugin-react tailwindcss postcss autoprefixer
cd ..
```

### 2. 환경 변수 설정

```bash
# server/.env
YOUTUBE_API_KEY=발급받은_YouTube_API_키
CLAUDE_API_KEY=발급받은_Claude_API_키
SERVER_PORT=4000
DB_PATH=../analysis.db

# client/.env
VITE_API_BASE_URL=http://localhost:4000
```

### 3. 실행

```bash
# React 앱 + Express 서버 동시 실행
npm run dev

# 첫 실행 시 analysis.db가 자동 생성됩니다
```

브라우저에서 `http://localhost:3000`에 접속합니다.

---

## 저장소 구조

```
competitive-intel/
├── client/          ← React 앱 (Vite)
├── server/          ← Express.js 서버
│   ├── db/          ← SQLite 연결 + 스키마
│   └── routes/      ← REST API 엔드포인트
├── analysis.db      ← SQLite DB (자동 생성, git 제외)
└── docs/
    ├── Development_Roadmap_Local.md
    └── Integrated_Design_Spec_Local.md
```

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/projects` | 분석 프로젝트 생성 |
| POST | `/api/sessions` | 검색 세션 저장 (entity_type: own/competitor/market) |
| POST | `/api/videos` | 영상 메타데이터 저장 |
| POST | `/api/analyses` | Claude 분석 결과 저장 |
| GET | `/api/projects/:id/compare` | 자사/경쟁사 집계 비교 |
| POST | `/api/reports` | 인사이트 리포트 생성 |
| GET | `/api/reports/:id/export` | Excel/PDF 파일 출력 |

---

## 두 저장소 역할 분담

| 기능 | 이 저장소 (Local) | youtube-reaction-app (Vercel) |
|------|:---:|:---:|
| 영상 수집 + 분석 표시 | ✅ | ✅ |
| 데이터 영구 저장 | ✅ SQLite | ❌ |
| 복수 세션 비교 | ✅ SQL | ❌ |
| 인사이트 리포트 출력 | ✅ Excel/PDF | ❌ |
| 외부 배포 | ❌ | ✅ Vercel |

---

## 문서

- [개발 로드맵](docs/Development_Roadmap_Local.md)
- [통합 설계 문서](docs/Integrated_Design_Spec_Local.md)
