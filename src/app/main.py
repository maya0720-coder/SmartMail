"""로컬 서버 실행 진입점.

사용법(저장소 루트, PYTHONPATH=src):
    .venv\\Scripts\\python -m app.main

- 바인딩 주소는 설정값(127.0.0.1 고정, 검증됨)만 쓴다. 명령줄로 바꿀 수 없다.
- 로그는 레코드 정화기를 거쳐 콘솔과 `SMARTMAIL_LOG_DIR/smartmail.log`에 남긴다.
- uvicorn의 기본 로그 설정은 쓰지 않고(log_config=None) 루트 설정을 따른다. Server 헤더는 끈다.
"""

from __future__ import annotations

import sys

import uvicorn

from app.bootstrap import build_container
from app.factory import create_app
from config.settings import ConfigError, load_settings
from security.logging_policy import configure_logging

LOG_FILE_NAME = "smartmail.log"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    configure_logging(settings.log_dir / LOG_FILE_NAME)
    container = build_container(settings)
    app = create_app(container)
    print(f"업무 처리 현황: http://{settings.app_host}:{settings.app_port}/ (종료: Ctrl+C)")
    uvicorn.run(
        app,
        host=settings.app_host,
        port=settings.app_port,
        log_config=None,
        server_header=False,
        proxy_headers=False,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
