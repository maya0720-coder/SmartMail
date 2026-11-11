"""실제 브라우저(Playwright, 비저장 컨텍스트)로 화면을 점검한다(12단계).

로컬 127.0.0.1에 데모 모드 서버를 띄우고, 외부 사이트에는 접속하지 않는다.
화면 캡처는 테스트 임시 폴더에만 저장한다.
Chromium이 설치되어 있지 않으면 건너뛴다.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from app.factory import create_app
from domain.clock import SystemClock
from fixtures.app_factory import make_settings

sync_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, object]]:
    from app.bootstrap import build_container
    from fixtures.app_factory import FakeBackend
    from security.browser import BrowserRuntime

    port = _free_port()
    settings = make_settings(tmp_path, app_port=port)
    container = build_container(
        settings, clock=SystemClock(), browser=BrowserRuntime(backend_factory=FakeBackend)
    )
    app = create_app(container, run_background_jobs=False)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, server_header=False, lifespan="on"
    )
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not instance.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert instance.started
    yield f"http://127.0.0.1:{port}", container
    instance.should_exit = True
    thread.join(timeout=10)


@pytest.mark.slow
def test_dashboard_grain_switch_and_xss(server: tuple[str, object], tmp_path: Path) -> None:
    base, _container = server
    try:
        playwright = sync_api.sync_playwright().start()
    except Exception as exc:  # pragma: no cover - 환경 의존
        pytest.skip(f"Playwright 시작 불가: {type(exc).__name__}")
    try:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - 브라우저 미설치
            pytest.skip(f"Chromium 실행 불가: {type(exc).__name__}")
        context = browser.new_context()  # 비저장 컨텍스트
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

        page.goto(base + "/")
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        xss = '<img src=x onerror="window.__xss=1"> 테스트 업무'
        response = page.request.post(
            base + "/api/tasks",
            data={"title": xss},
            headers={"X-CSRF-Token": token or "", "Content-Type": "application/json"},
        )
        assert response.ok
        page.reload()
        page.wait_for_selector("#todo tr")
        for grain in ("year", "half", "quarter", "month", "week"):
            page.click(f'#seg button[data-g="{grain}"]')
            page.wait_for_timeout(150)
            assert page.locator("#mk .kpi").count() == 4
        assert page.evaluate("window.__xss") is None
        assert page.locator("#todo", has_text="테스트 업무").count() == 1
        for path in ("/suggestions", "/weekly", "/history", "/settings"):
            page.goto(base + path)
            page.wait_for_timeout(200)
        page.screenshot(path=str(tmp_path / "dashboard.png"))  # 로컬 임시 폴더에만 저장
        context.close()
        browser.close()
        assert errors == []
    finally:
        playwright.stop()
