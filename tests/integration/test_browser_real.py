"""실제 Chromium으로 비저장 컨텍스트·임시 폴더 삭제 확인(AUTH-06). 외부 사이트 접속 없음."""

from __future__ import annotations

import pytest

from security.browser import BrowserRuntime

pytest.importorskip("playwright.sync_api")


@pytest.mark.slow
def test_auth06_real_chromium_temp_dir_removed() -> None:
    runtime = BrowserRuntime()
    try:
        try:
            context = runtime.open_context(headless=True)
        except Exception as exc:  # pragma: no cover - 브라우저 미설치 환경
            pytest.skip(f"Chromium 실행 불가: {type(exc).__name__}")
        temp_dir = runtime.temp_dir
        assert temp_dir is not None and temp_dir.exists()
        title = context.call(
            lambda raw: (page := raw.new_page()).set_content("<title>로컬</title>") or page.title()
        )
        assert title == "로컬"
        cookies = context.call(lambda raw: raw.cookies())
        assert cookies == []
    finally:
        runtime.shutdown()
    assert temp_dir is not None and not temp_dir.exists()
