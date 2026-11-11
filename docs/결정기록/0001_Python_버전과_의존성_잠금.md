# 0001. Python 버전과 의존성 잠금 방식

- 날짜: 2026-11-11
- 단계: 01
- 상태: 적용(2026-11-11 사용자 동의)

## 결정

1. 개발·실행 Python은 **3.11**(설치 확인: 3.11.9)로 하고 `.venv`에 가상환경을 만든다.
   - PC 기본 Python은 3.15.2지만, pywin32·playwright 등 네이티브 의존성의 신규 버전 지원이 늦을 수 있어 개발 지침서의 최소 버전(3.11)을 쓴다.
   - `pyproject.toml`의 `requires-python`을 `>=3.11,<3.12`로 고정한다. 올릴 때는 이 문서를 갱신한다.
2. 의존성 잠금은 **pip-tools**로 해시 포함 잠금 파일을 만든다.
   - 운영: `requirements.lock` / 개발: `requirements-dev.lock`(pip·setuptools 포함 고정)
   - 갱신: `.venv\Scripts\pip-compile --generate-hashes --strip-extras -o requirements.lock pyproject.toml`
     및 `.venv\Scripts\pip-compile --generate-hashes --strip-extras --allow-unsafe --extra dev -o requirements-dev.lock pyproject.toml`
   - 설치: `.venv\Scripts\pip-sync requirements-dev.lock`
3. 시크릿 탐지는 **detect-secrets**(Python 패키지, 별도 바이너리 불필요), 의존성 취약점 검사는 **pip-audit**를 쓴다.
4. `src/` 아래 패키지(`app`, `domain` 등)는 최상위 패키지로 import한다(pytest `pythonpath`, mypy `mypy_path`를 `src`로 설정). 개발 지침 0-1장의 폴더 구조를 그대로 따르기 위함이다.

## 의존성 점검 결과(2026-11-11)

- `pip-audit --strict -r requirements-dev.lock`: 알려진 취약점 없음
- 운영 후보: fastapi 0.162.4, uvicorn 0.54.0, jinja2 3.1.6, pydantic-settings 2.16.2, playwright 1.65.0, pywin32 312(Windows 전용)
- 개발: pytest 9.2.0, ruff 0.17.4, mypy 2.4.1, detect-secrets 1.5.0, pip-audit 2.10.1, pip-tools 7.6.2

## 확인 필요

- ~~Python 3.11 사용에 대한 사용자 확인~~ → 2026-11-11 동의
- playwright 브라우저 바이너리 설치(`playwright install`)는 08단계에서 사내 허용 여부 확인 후 진행
