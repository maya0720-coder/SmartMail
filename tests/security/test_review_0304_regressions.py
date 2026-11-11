"""03·04단계 보안 검토 지적 회귀 테스트(결정기록 0005)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.factory import create_app
from config.settings import PROJECT_ROOT
from domain.clock import FixedClock
from domain.models import Task, TaskSource, TaskStatus
from fixtures.app_factory import BASE_URL, make_container, make_settings
from security.logging_policy import Site
from security.masking import mask
from security.residue_check import ResidueChecker
from security.sessions import SessionStore
from storage.db import Database
from storage.knowledge_repos import AiCallRecord, SqliteAiCallLog
from storage.repositories import SqliteTaskRepository


class Closable:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_m1_log_dir_must_be_dedicated(tmp_path: Path) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_settings(tmp_path, log_dir=PROJECT_ROOT)
    with pytest.raises(ValidationError):
        make_settings(tmp_path, log_dir=tmp_path / "data")  # DB 폴더와 같음


def test_m1_residue_only_top_level_log_files(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    (logs / "nested").mkdir(parents=True)
    nested = logs / "nested" / "app.log"
    nested.write_text("password: x\n", encoding="utf-8")
    source_like = logs / "notes.py"
    source_like.write_text("password: str = Field()\n", encoding="utf-8")
    top = logs / "app.log.1"
    top.write_text("Cookie: abc\n", encoding="utf-8")
    (tmp_path / "temp").mkdir()
    checker = ResidueChecker(
        project_root=tmp_path / "project", log_dir=logs, temp_root=tmp_path / "temp"
    )
    assert checker.run().deleted == 1
    assert not top.exists()
    assert nested.exists() and source_like.exists()


def test_m2_sweep_closes_idle_sessions_without_calls(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30))
    native = Closable()
    store.open(Site.WIKI, native)
    clock.advance(timedelta(minutes=31))
    store.sweep()
    assert native.closed


def test_m3_non_loopback_client_rejected(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    app = create_app(container, run_background_jobs=False)  # 기본: 루프백만 허용
    with TestClient(app, base_url=BASE_URL) as client:
        assert client.get("/settings").status_code == 403


def test_l5_closed_task_requires_closed_at(db: Database, clock: FixedClock) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        SqliteTaskRepository(db).add(Task("완료", TaskStatus.DONE, TaskSource.MANUAL, clock.now()))


def test_l7_ai_call_log_requires_masked_text(db: Database, clock: FixedClock) -> None:
    log = SqliteAiCallLog(db)
    with pytest.raises(TypeError):
        log.add(AiCallRecord(clock.now(), "원문 그대로", True))  # type: ignore[arg-type]
    log.add(AiCallRecord(clock.now(), mask("연락처 010-1234-5678 문의"), True))
    [record] = log.list_recent()
    assert "010-1234-5678" not in record.request_summary.value
