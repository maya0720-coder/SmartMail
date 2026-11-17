"""[설정·연결] 데이터 모드 화면 테스트(결정기록 0042). 로컬 데모 서버만 쓴다."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.run_mode import load_saved_mode, run_mode_path
from fixtures.ui_server import chromium, demo_server


@pytest.mark.slow
def test_ui_settings_run_mode_saves_for_next_start(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(base + "/settings")
        page.wait_for_selector("#rm-current:has-text('가상 데이터')")
        page.select_option("#rm-mode", "real")
        page.click("#rm-form button[type=submit]")
        page.wait_for_selector("#rm-note:has-text('다시 실행하면 적용')")
        assert load_saved_mode(run_mode_path(container.settings)) == "real"
        assert "가상 데이터" in page.inner_text("#rm-current")  # 지금 실행은 그대로
        assert errors == []
