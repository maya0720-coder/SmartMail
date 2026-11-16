# 하네스 폴더 구조 상세

새 파일 위치를 정할 때 읽는다. 규칙 요약은 `CLAUDE.md`의 '하네스 규칙'에 있다.

```text
mail-work-status/
├─ CLAUDE.md                # 개발 지침
├─ .claude/
│  ├─ settings.json         # 권한·훅 설정
│  ├─ agents/               # 보안 검토·테스트 작성·기획 대조 서브에이전트
│  ├─ commands/             # 반복 명령(점검, 보안 검토 등)
│  ├─ docs/                 # 필요할 때만 읽는 상세 지침(이 파일)
│  └─ hooks/                # 시크릿 탐지·포맷 자동 실행
├─ docs/
│  ├─ 기획서.md
│  ├─ 요구사항추적표.md
│  ├─ 결정기록/
│  └─ 변경이력.md
├─ src/
│  ├─ app/                  # 화면·API 라우터, 정적 리소스
│  ├─ domain/               # 업무·주간보고·지식·성과 지표 규칙(순수 로직)
│  ├─ collectors/           # 아웃룩(COM)·그룹웨어 메일 수집
│  ├─ integrations/         # 위키·G-Knowledge·AI-Ground 어댑터
│  ├─ security/             # 자격증명(비저장)·세션·마스킹
│  ├─ storage/              # 로컬 DB·보관기간 삭제·지표 원장
│  └─ config/               # 설정 로딩·검증
├─ tests/
│  ├─ unit/  integration/  security/
│  └─ fixtures/             # 합성 데이터만
├─ scripts/                 # 로컬 실행·점검
└─ .env.example             # placeholder만
```

성격별 폴더: 화면·API → `app/` · 업무 규칙 → `domain/` · 수집 → `collectors/` · 연동 → `integrations/` · 보안 → `security/` · 저장 → `storage/` · 설정 → `config/` · 테스트 → `tests/` · 문서 → `docs/`.
