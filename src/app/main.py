"""로컬 서버 실행 진입점.

사용법(저장소 루트, PYTHONPATH=src):
    .venv\\Scripts\\python -m app.main

- 바인딩 주소는 설정값(127.0.0.1 고정, 검증됨)만 쓴다. 명령줄로 바꿀 수 없다.
- 로그는 레코드 정화기를 거쳐 콘솔과 `SMARTMAIL_LOG_DIR/smartmail.log`에 남긴다.
- uvicorn의 기본 로그 설정은 쓰지 않고(log_config=None) 루트 설정을 따른다. Server 헤더는 끈다.
"""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser

import uvicorn

from app.bootstrap import build_container
from app.factory import create_app
from config.settings import ConfigError, load_settings
from security.logging_policy import configure_logging

LOG_FILE_NAME = "smartmail.log"
BROWSER_OPEN_DELAY_SECONDS = 2.0


def _open_browser_later(url: str) -> None:
    """서버가 뜰 시간을 두고 기본 브라우저로 화면을 연다(실행 파일용 `--open`)."""
    timer = threading.Timer(BROWSER_OPEN_DELAY_SECONDS, webbrowser.open, args=(url,))
    timer.daemon = True
    timer.start()


def _port_in_use(host: str, port: int) -> bool:
    """같은 주소에 이미 서버가 떠 있는지(앱 중복 실행 방지, 로컬실행가이드 1장)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    open_browser = "--open" in sys.argv[1:]
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    url = f"http://{settings.app_host}:{settings.app_port}/"
    if _port_in_use(settings.app_host, settings.app_port):
        # 두 번째 실행은 잔존 검사가 실행 중인 앱의 브라우저 임시 폴더를 지울 수 있다.
        # 그래서 새로 띄우지 않고 기존 화면만 연다.
        print(f"이미 실행 중입니다: {url}")
        print("다시 시작하려면 실행 중인 창에서 Ctrl+C로 종료한 뒤 실행하세요.")
        if open_browser:
            webbrowser.open(url)
        return 3
    configure_logging(settings.log_dir / LOG_FILE_NAME)
    container = build_container(settings)
    app = create_app(container)
    print(f"업무 처리 현황: {url} (종료: Ctrl+C)")
    if open_browser:
        _open_browser_later(url)
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
