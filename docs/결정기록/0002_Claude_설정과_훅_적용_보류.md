# 0002. `.claude/settings.json`·`.claude/hooks/` 적용 보류

- 날짜: 2026-11-11
- 단계: 01(작업 5번)
- 상태: 보류(사용자 직접 적용 필요)

## 배경

01단계 작업 5번은 `.claude/settings.json`(위험 명령 확인)과 `.claude/hooks/`(편집 후 ruff format, 커밋 전 시크릿 탐지)를 만들도록 한다.
그러나 상위 워크스페이스의 `claude-guard` hook이 `.claude/settings*.json`, `.claude/hooks/` 변경을 차단하므로 Claude Code가 직접 만들 수 없다. 지침상 우회하지 않는다.

## 이미 적용되는 통제

- 상위 `claude-guard`: `git push`, `git remote` 변경, 패키지 배포, 파일 업로드 시 사용자 확인 / 비밀정보·주민번호·카드번호 작성 차단 / 500줄 초과 파일 차단
- 따라서 "원격 push 확인 요구"는 이미 충족된다.

## 사용자가 직접 적용할 내용(제안)

`.claude/settings.json`:

```json
{
  "permissions": {
    "ask": ["Bash(rm -r:*)", "Bash(rm -rf:*)", "PowerShell(Remove-Item:*)"]
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          { "type": "command", "command": ".venv\\Scripts\\python -m ruff format --quiet src tests scripts" }
        ]
      }
    ]
  }
}
```

- 커밋 전 시크릿 탐지는 Claude hook 대신 git `pre-commit` 훅(`.git/hooks/pre-commit`에서 `.venv\Scripts\python scripts\check.py` 실행 또는 detect-secrets 단독 실행)으로 두는 방안도 있다.
- 적용 후 이 문서의 상태를 `적용`으로 바꾼다.
