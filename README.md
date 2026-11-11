# 업무 처리 관리 현황 — 개발 문서 패키지 v1.0 (2026-11-11)

## 사용 방법

1. 이 폴더 전체를 개발 저장소 루트에 복사한다(예: `D:\00AI_Workspace\Codex\Mail`).
2. 그 폴더에서 Claude Code를 실행한다. `CLAUDE.md`가 자동으로 읽힌다.
3. 첫 지시: "`dev/00_MAIN_개발진행.md`를 읽고 01단계부터 진행해"
4. 이후: "다음 단계 진행해" 또는 "끝까지 진행해(확인 필요 항목에서 멈춤)"

## 구성

| 경로 | 내용 |
| --- | --- |
| `CLAUDE.md` | 개발 지침(하네스 구조·보안·개인정보·작업 절차) |
| `dev/00_MAIN_개발진행.md` | 메인 문서: 실행 규칙, 진행 현황표, 보고 형식, 미확정 사항 |
| `dev/01` ~ `dev/13` | 개발 순서별 단계 문서 |
| `docs/기획서_v1.0.md` · `docs/기획서_그림/` | 요구사항 원본 |
| `docs/개발지침서_v1.0.md` | 개발 지침 원본 백업 |
| `docs/샘플화면_v1.0.html` | 화면 기준(가상 데이터) |
| `docs/요구사항추적표.md` | 요구사항 ID ↔ 단계 ↔ 구현·테스트 추적 |

## 앱 실행(로컬 테스트 버전)

- 실행 방법: `docs/로컬실행가이드.md`
- 데모 실행: `powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Install -Demo`
- 실제 연동 전 수동 점검: `docs/수동점검.md`
- 점검: `.venv\Scripts\python scripts\check.py --audit`
