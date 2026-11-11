"""로컬 DB·저장소 테스트(03단계, MAIL-08)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from domain.clock import FixedClock
from domain.models import (
    HistoryCard,
    LedgerEntry,
    MailRef,
    MailSource,
    ResolutionStage,
    Task,
    TaskEventType,
    TaskSource,
    TaskStatus,
)
from storage.db import Database
from storage.knowledge_repos import SqliteHistoryRepository, SqliteLedgerRepository
from storage.repositories import (
    SqliteEventRecorder,
    SqliteMailRefRepository,
    SqliteTaskRepository,
)


def _task(clock: FixedClock, **kw: object) -> Task:
    base: dict[str, object] = {
        "title": "FI 전표 오류 확인",
        "status": TaskStatus.WAITING,
        "source": TaskSource.MAIL,
        "created_at": clock.now(),
    }
    base.update(kw)
    return Task(**base)  # type: ignore[arg-type]


def test_task_round_trip(db: Database, clock: FixedClock) -> None:
    repo = SqliteTaskRepository(db)
    saved = repo.add(_task(clock, due_at=clock.now() + timedelta(days=2), thread_id="T1"))
    assert saved.id is not None
    loaded = repo.get(saved.id)
    assert loaded == saved
    assert repo.find_by_thread("T1") == saved


def test_task_update_and_filter(db: Database, clock: FixedClock) -> None:
    repo = SqliteTaskRepository(db)
    saved = repo.add(_task(clock))
    from dataclasses import replace

    repo.update(replace(saved, status=TaskStatus.IN_PROGRESS))
    assert [t.id for t in repo.list_tasks([TaskStatus.IN_PROGRESS])] == [saved.id]
    assert repo.list_tasks([TaskStatus.DONE]) == []


def test_naive_datetime_rejected(db: Database) -> None:
    from datetime import datetime

    with pytest.raises(ValueError):
        SqliteTaskRepository(db).add(
            Task("x", TaskStatus.WAITING, TaskSource.MANUAL, datetime(2026, 1, 1))  # noqa: DTZ001
        )


def test_title_length_enforced_by_schema(db: Database, clock: FixedClock) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        SqliteTaskRepository(db).add(_task(clock, title="가" * 201))


def test_sql_injection_value_stored_as_text(db: Database, clock: FixedClock) -> None:
    repo = SqliteTaskRepository(db)
    title = "x'); DROP TABLE task; --"
    saved = repo.add(_task(clock, title=title))
    assert saved.id is not None
    assert repo.get(saved.id) is not None
    assert repo.list_tasks()[0].title == title


def test_mail08_mail_ref_stores_reference_only(db: Database, clock: FixedClock) -> None:
    tasks = SqliteTaskRepository(db)
    refs = SqliteMailRefRepository(db)
    task = tasks.add(_task(clock))
    assert task.id is not None
    ref = refs.add(
        MailRef(MailSource.OUTLOOK, "ENTRY-1", "<m1@example.invalid>", "T1", clock.now())
    )
    assert ref.id is not None
    refs.link_task(ref.id, task.id)
    refs.set_summary(ref.id, "결산 일정 문의")
    [loaded] = refs.list_for_task(task.id)
    assert loaded.summary == "결산 일정 문의"
    assert refs.find_by_message_id("<m1@example.invalid>") == loaded


def test_event_recorder_filters(db: Database, clock: FixedClock) -> None:
    recorder = SqliteEventRecorder(db, clock)
    recorder.record(TaskEventType.RECEIVED)
    clock.advance(timedelta(hours=1))
    recorder.record(TaskEventType.SEARCH_RESOLVED, detail=ResolutionStage.APP.value)
    events = recorder.list_events(kinds=[TaskEventType.SEARCH_RESOLVED])
    assert [e.detail for e in events] == ["app"]
    assert len(recorder.list_events(since=clock.now())) == 1


@pytest.mark.parametrize("detail", ["자유 텍스트 메모", "a" * 41, "x;y"])
def test_event_detail_rejects_free_text(db: Database, clock: FixedClock, detail: str) -> None:
    with pytest.raises(ValueError):
        SqliteEventRecorder(db, clock).record(TaskEventType.FEEDBACK, detail=detail)


def test_history_card_upsert(db: Database, clock: FixedClock) -> None:
    task = SqliteTaskRepository(db).add(_task(clock))
    assert task.id is not None
    repo = SqliteHistoryRepository(db)
    card = HistoryCard(task.id, "문제", "원인", "조치", clock.now(), tags=("FI", "결산"))
    repo.add(card)
    repo.add(HistoryCard(task.id, "문제2", "원인", "조치", clock.now()))
    loaded = repo.get_for_task(task.id)
    assert loaded is not None and loaded.problem == "문제2"
    assert len(repo.list_cards()) == 1


def test_ledger_upsert_keeps_gk_flag(db: Database, clock: FixedClock) -> None:
    repo = SqliteLedgerRepository(db)
    repo.upsert(LedgerEntry(1, clock.now(), "본인", has_due=True, due_met=True))
    repo.mark_gk_registered(1)
    repo.set_resolution(1, ResolutionStage.GKNOWLEDGE)
    repo.upsert(LedgerEntry(1, clock.now(), "본인", has_due=True, due_met=False))
    [entry] = repo.list_entries()
    assert entry.gk_registered is True
    assert entry.due_met is False
    assert entry.resolution_stage is ResolutionStage.GKNOWLEDGE


def test_ret_full_wipe_leaves_no_rows(db: Database, clock: FixedClock) -> None:
    tasks = SqliteTaskRepository(db)
    task = tasks.add(_task(clock))
    assert task.id is not None
    SqliteEventRecorder(db, clock).record(TaskEventType.SUGGESTED, task_id=task.id)
    SqliteLedgerRepository(db).upsert(LedgerEntry(task.id, clock.now(), "본인", False, None))
    assert db.wipe_all() >= 3
    for table in db.table_columns():
        rows = db.query(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608
        assert rows[0]["n"] == 0


def test_db_file_created_under_given_path(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "app.db"
    database = Database(path)
    database.close()
    assert path.exists()
