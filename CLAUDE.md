# CLAUDE.md — 업무 처리 관리 현황 (SmartMail, 로컬 테스트 버전)

## 명령어

```bash
.venv\Scripts\python scripts\check.py            # 포맷·린트·타입·테스트·시크릿·금지 패턴 일괄 점검(완료 전 필수)
.venv\Scripts\python scripts\check.py --audit    # + 의존성 취약점 검사(pip-audit, 네트워크 필요)
.venv\Scripts\python -m pytest tests/unit -q     # 부분 테스트
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Install -Demo   # 데모 실행(가상 데이터)
업무처리현황_실행.bat                             # 로컬 실행(127.0.0.1)
```

## 시작·참조 문서

- 개발 시작·재개: `dev/00_MAIN_개발진행.md`를 먼저 읽고, 지정 순서대로 단계 파일을 하나씩 실행.
- 요구사항 원본: `docs/기획서_v1.0.md`(ID: MAIL·TODO·WIKI·HIST·AI·GK·AUTH·RET·SRCH, 성과 지표 MET). 코드는 요구사항 ID 기준.
- 화면 기준: `docs/샘플화면_v1.0.html`(가상 데이터) · 추적: `docs/요구사항추적표.md` · 이력: `docs/변경이력.md`
- 폴더 구조 상세: `.claude/docs/harness.md` — 새 파일 위치를 정할 때 읽기.

## 공통 지침(상위 파일)

- 기본 원칙·보안 코딩·개인정보·외부 노출 금지·하드코딩 금지·작업 절차·테스트 기준·완료 점검표는 상위 `D:\00AI_Workspace\Claude\CLAUDE.md`(자동 로드)를 그대로 따른다. 이 파일은 프로젝트 추가 규칙만 둔다.
- 이 저장소는 상위 지침보다 엄격하게: **개인정보는 절대 저장하지 않는다**(상위의 '근거 확인된 개인정보만 저장' 대신 적용).
- 법령·고시는 착수·배포 시점 최신 내용 재확인. 이 지침은 법률 자문이 아니다.

## 작업 환경

- 설명 문구·md 문서·피드백(진행·질문·최종 보고)은 한글. 소스 코드는 예외.
- AXHub AI 활용 기록을 켜고 작업(설정은 사내 AXHub 안내, 확인 필요).
- 테스트 버전은 로컬 실행. 이후 IE팀 운영 사내 웹 배포로 옮길 수 있게 화면·업무 규칙·연동 분리.
- 권장 스택(확정 필요): Python + FastAPI, pywin32(클래식 아웃룩 COM), Playwright(브라우저 자동화), SQLite, 서버 렌더링 HTML + JS, pytest·ruff·mypy.

## 하네스 규칙

1. 구조 폴더가 없으면 `.claude/docs/harness.md` 구조대로 생성. 새 파일은 성격에 맞는 폴더에만. 애매하면 만들기 전에 위치 보고.
2. 의존 방향 `app → domain ← (collectors, integrations, storage)`. domain은 메일·외부 사이트·DB 직접 호출 금지, 인터페이스만.
3. 아웃룩·그룹웨어·위키·G-Knowledge·AI-Ground 연동은 `collectors/`·`integrations/` 어댑터로만. 어댑터마다 Fake 구현. 테스트는 실제 사이트 접속 금지.
4. ID·암호는 `security/` 자격증명 모듈만 취급, 로그인 1회 후 메모리에서 즉시 폐기. 다른 모듈은 세션 핸들만. 브라우저 자동화는 비저장 프로필만(기획서 4.7).
5. 위키·G-Knowledge·g-llm 전송 데이터는 `security/` 마스킹 필수. 전송 함수는 마스킹 타입만 받게 해 누락을 코드로 차단.
6. 메일 원문 저장 금지, 참조 ID만. 완료 후 7일 삭제는 `storage/` 보관기간 작업. 지표 원장 예외는 기획서 결정 후 적용.
7. 로컬 서버는 127.0.0.1만 바인딩. 주소·포트·외부 URL은 `config/`·`.env`로 주입, `.env.example`은 placeholder만.
8. 요구사항 ID를 테스트 이름·커밋 메시지·`docs/요구사항추적표.md`에 연결.
9. 순서: 계획(요구사항 ID 확인) → 구현 → 검증(포맷·린트·타입·테스트·시크릿 탐지) → 한글 보고. 시크릿 탐지·포맷은 `.claude/hooks`로 자동 실행.
10. 기획서와 다르게 판단하면 `docs/결정기록/`에 이유를 남기고 사용자 확인.

## 완료 점검(상위 점검표에 추가)

- [ ] 파일이 하네스 구조의 알맞은 폴더에 있고, 요구사항 ID가 테스트·추적표에 연결됐다.
- [ ] 설명 문구·md·보고가 한글로 작성됐다.
