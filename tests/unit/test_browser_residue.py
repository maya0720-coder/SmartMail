"""비저장 브라우저 실행기·잔존 검사 테스트(AUTH-06, AUTH-07). 실제 브라우저는 띄우지 않는다."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

import pytest

from fixtures.app_factory import FakeBackend
from security.browser import TEMP_PREFIX, BrowserRuntime
from security.residue_check import ResidueChecker


def test_auth06_shutdown_removes_temp_profile() -> None:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    context = runtime.open_context()
    temp_dir = runtime.temp_dir
    assert temp_dir is not None and (temp_dir / "profile" / "Cookies").exists()
    assert temp_dir.name.startswith(TEMP_PREFIX)
    runtime.shutdown()
    assert context.closed
    assert not temp_dir.exists()
    assert FakeBackend.instances[-1].closed


def test_context_call_runs_on_single_browser_thread() -> None:
    import threading

    runtime = BrowserRuntime(backend_factory=FakeBackend)
    try:
        context = runtime.open_context()
        names = {context.call(lambda raw: threading.current_thread().name) for _ in range(3)}
        assert len(names) == 1 and next(iter(names)).startswith("smartmail-browser")
        context.close()
        with pytest.raises(RuntimeError):
            context.call(lambda raw: None)
    finally:
        runtime.shutdown()


def _checker(tmp_path: Path, extra: tuple[Path, ...] = ()) -> ResidueChecker:
    (tmp_path / "project").mkdir(exist_ok=True)
    (tmp_path / "temp").mkdir(exist_ok=True)
    return ResidueChecker(
        project_root=tmp_path / "project",
        log_dir=tmp_path / "logs",
        temp_root=tmp_path / "temp",
        extra_scan_dirs=extra,
    )


def test_auth07_leftover_profiles_deleted(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    checker = _checker(tmp_path)
    leftover = tmp_path / "temp" / f"{TEMP_PREFIX}old"
    (leftover / "Default").mkdir(parents=True)
    (leftover / "Default" / "Cookies").write_bytes(b"x")
    profile = tmp_path / "project" / "browser-profile"
    profile.mkdir()
    other_app = tmp_path / "temp" / "other-app-cache"
    other_app.mkdir()
    with caplog.at_level(logging.INFO):
        report = checker.run()
    assert report.found == 2 and report.deleted == 2
    assert not leftover.exists() and not profile.exists()
    assert other_app.exists()  # 다른 프로그램 임시 폴더는 건드리지 않는다
    assert "발견 2건, 삭제 2건" in caplog.text


def test_auth07_active_temp_dir_kept(tmp_path: Path) -> None:
    checker = _checker(tmp_path)
    active = tmp_path / "temp" / f"{TEMP_PREFIX}now"
    active.mkdir()
    assert checker.run(active_temp_dir=active).found == 0
    assert active.exists()


def test_auth07_log_with_cookie_header_deleted(tmp_path: Path) -> None:
    checker = _checker(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    bad = logs / "old.log"
    bad.write_text("Set-Cookie: abc\n", encoding="utf-8")
    good = logs / "app.log"
    good.write_text("event=login site=wiki result=success\n", encoding="utf-8")
    report = checker.run()
    assert report.deleted == 1
    assert not bad.exists() and good.exists()


def test_auth07_marker_in_extra_dir_deleted(tmp_path: Path) -> None:
    scan = tmp_path / "scan"
    scan.mkdir()
    marker = secrets.token_hex(10).encode()
    target = scan / "dump.tmp"
    target.write_bytes(b"prefix " + marker)
    checker = _checker(tmp_path, (scan,))
    assert checker.run([marker]).deleted == 1
    assert not target.exists()
