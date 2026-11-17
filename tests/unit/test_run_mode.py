"""데이터 모드(실제/가상) 화면 선택 테스트(결정기록 0042). 합성 설정만 쓴다."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.run_mode import (
    DEMO_ENV,
    apply_run_mode,
    load_saved_mode,
    run_mode_path,
    save_mode,
)
from config.settings import ConfigError
from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container, make_settings


def test_run_mode_save_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "data" / "run_mode.json"
    assert load_saved_mode(path) is None
    save_mode(path, "demo")
    assert load_saved_mode(path) == "demo"
    save_mode(path, "real")
    assert load_saved_mode(path) == "real"
    assert path.read_text(encoding="utf-8") == '{"mode": "real"}'
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize(
    "content",
    ['{"mode": "prod"}', "not json", '{"mode": "demo", "x": 1}', "[]", '{"mode": "' + "a" * 300],
)
def test_run_mode_bad_file_fails_start(tmp_path: Path, content: str) -> None:
    path = tmp_path / "run_mode.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_saved_mode(path)


def test_run_mode_precedence(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, demo_mode=False)
    assert apply_run_mode(settings, {}).demo_mode is True  # 저장값 없음 → 기본 가상
    save_mode(run_mode_path(settings), "real")
    assert apply_run_mode(settings, {}).demo_mode is False  # 화면에서 실제 선택
    save_mode(run_mode_path(settings), "demo")
    assert apply_run_mode(settings, {}).demo_mode is True  # 화면 선택값
    forced = apply_run_mode(settings, {DEMO_ENV: "false"})
    assert forced.demo_mode is False  # 실행 스크립트 환경 변수가 우선


def test_run_mode_api(tmp_path: Path, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEMO_ENV, raising=False)
    container = make_container(tmp_path, clock)  # 테스트 컨테이너는 데모 모드
    with app_client(container) as client:
        state = client.get("/api/run-mode").json()
        assert state == {
            "current": "demo",
            "saved": None,
            "default": "demo",
            "forced": False,
            "restart_needed": False,
        }
        saved = client.put("/api/run-mode", json={"mode": "real"}).json()
        assert saved["saved"] == "real" and saved["restart_needed"] is True
        assert load_saved_mode(run_mode_path(container.settings)) == "real"
        assert client.put("/api/run-mode", json={"mode": "prod"}).status_code == 422
        assert client.put("/api/run-mode", json={"mode": "demo", "x": 1}).status_code == 422
        back = client.put("/api/run-mode", json={"mode": "demo"}).json()
        assert back["restart_needed"] is False
        # CSRF 토큰 없는 변경은 거부
        client.headers.pop("X-CSRF-Token")
        assert client.put("/api/run-mode", json={"mode": "real"}).status_code == 403
    assert load_saved_mode(run_mode_path(container.settings)) == "demo"
