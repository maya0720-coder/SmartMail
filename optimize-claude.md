# Task: Optimize CLAUDE.md & Reduce Session Context Overhead

현재 프로젝트 루트의 `CLAUDE.md` 파일(약 150줄)과 프로젝트 구조를 분석하여, 매 턴 소모되는 기본 시스템 토큰을 최소화하고 컨텍스트 효율을 극대화하는 작업을 일괄 수행하라.

## Execution Steps

### Step 1. 현재 상태 진단

1. 루트 디렉토리의 `CLAUDE.md`를 읽고 현재 줄 수 및 예상 토큰 수를 측정하라.
2. 프로젝트 디렉토리 구조를 스캔하여 대용량 빌드 산출물, 로그, 락 파일, 미디어/바이너리 폴더 여부를 확인하라.

### Step 2. `CLAUDE.md` 압축 및 재구성 (목표: 60~80줄 이내)

기존 `CLAUDE.md`의 내용을 아래 원칙에 따라 압축하라. **기존의 핵심 규칙과 명령어는 절대 누락하지 말 것.**

1. **제거 대상**:
   - 일반 코딩 상식, 장황한 배경 설명, 중복 문장 삭제
   - Prettier/ESLint/Ruff 등 포매터·린터로 강제되는 단순 코드 스타일 설명 삭제 (대신 린트 실행 명령어 1줄만 유지)
2. **문체 변환**:
   - 모든 서술형 문장을 짧은 개조식(Bullet point) 및 키워드 중심으로 압축
   - 명령어는 실행 가능한 코드 블록(`bash`)으로 최상단에 배치
3. **도메인 상세 규칙 분리 (Progressive Disclosure)**:
   - 특정 모듈(DB, 인증, 결제, 배포 등)에만 해당하는 상세 규칙이나 긴 예시는 `.claude/docs/` 하위 마크다운 파일로 분리하라.
   - `CLAUDE.md` 본문에는 `@.claude/docs/<파일명>.md` 형태의 경로 포인터와 1줄 요약만 남겨 필요할 때만 로드되게 하라.

### Step 3. `.claudeignore` 생성 또는 업데이트

불필요한 파일 탐색으로 세션 컨텍스트가 낭비되지 않도록 루트에 `.claudeignore` 파일을 생성(기존 파일이 있으면 병합)하라.

- 필수 포함 항목: `node_modules/`, `.git/`, `dist/`, `build/`, `.next/`, `coverage/`, `*.log`, `*.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `.env*`, 바이너리/에셋 디렉토리.

### Step 4. 검증 및 결과 보고

1. 수정 전후 `CLAUDE.md`의 줄 수와 예상 토큰 절감률을 표로 요약해 보고하라.
2. `.claude/docs/`로 분리된 파일 목록과 각 파일의 역할을 명시하라.
3. 변경 사항 적용 후, 유저가 실행할 다음 권장 명령어(`/context`, `/compact`)를 안내하라.

## Constraints

- 기존 `CLAUDE.md`에 명시된 금지 사항(Don't), 환경 변수 규칙, 테스트/빌드 명령어는 100% 보존할 것.
- 작업 완료 전 기존 `CLAUDE.md`의 핵심 내용이 손실되지 않았는지 교차 검증할 것.
