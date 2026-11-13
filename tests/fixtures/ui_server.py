"""브라우저 테스트용 데모 서버·도우미(127.0.0.1, 가짜 어댑터, 외부 접속 없음)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from app.bootstrap import build_container
from app.container import Container
from app.factory import create_app
from domain.clock import SystemClock
from fixtures.app_factory import FakeBackend, make_settings
from security.browser import BrowserRuntime


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def demo_server(tmp_path: Path) -> Iterator[tuple[str, Container]]:
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
    try:
        yield f"http://127.0.0.1:{port}", container
    finally:
        instance.should_exit = True
        thread.join(timeout=10)


@contextmanager
def chromium() -> Iterator[Any]:
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        playwright = sync_api.sync_playwright().start()
    except Exception as exc:  # pragma: no cover - 환경 의존
        pytest.skip(f"Playwright 시작 불가: {type(exc).__name__}")
    try:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - 브라우저 미설치
            pytest.skip(f"Chromium 실행 불가: {type(exc).__name__}")
        try:
            yield browser
        finally:
            browser.close()
    finally:
        playwright.stop()


def csrf_post(page: Any, base: str, path: str, data: dict[str, Any] | None = None) -> Any:
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    response = page.request.post(
        base + path,
        data=data or {},
        headers={"X-CSRF-Token": token or "", "Content-Type": "application/json"},
    )
    assert response.ok, path
    return response.json()
