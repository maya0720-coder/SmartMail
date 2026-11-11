"""보관기간 삭제 테스트(RET-01, RET-04)."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest

from app.scheduler import PeriodicJob
from domain.clock import FixedClock
from domain.models import (
    HistoryCard,
    LedgerEntry,
    MailRef,
    MailSource,
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
from storage.retention import RetentionJob


def _closed_task(db: Database, clock: FixedClock, *, closed_ago: timedelta, n: int = 1) -> int:
    now = clock.now()
    task = SqliteTaskRepository(db).add(
        Task(
            "완료 업무",
            TaskStatus.DONE,
            TaskSource.MAIL,
            now - closed_ago - timedelta(days=1),
            closed_at=now - closed_ago,
            thread_id=f"T{n}",
        )
    )
    assert task.id is not None
    ref = SqliteMailRefRepository(db).add(
        MailRef(MailSource.OUTLOOK, f"E{n}", f"<m{n}@x.invalid>", f"T{n}", now - closed_ago)
    )
    assert ref.id is not None
    SqliteMailRefRepository(db).link_task(ref.id, task.id)
    SqliteEventRecorder(db, clock).record(TaskEventType.COMPLETED, task_id=task.id)
    SqliteHistoryRepository(db).add(HistoryCard(task.id, "문제", "원인", "조치", now))
    SqliteLedgerRepository(db).upsert(
        LedgerEntry(task.id, now - closed_ago, "본인", has_due=False, due_met=None)
    )
    return task.id


def _job(db: Database, clock: FixedClock, keep: bool = False) -> RetentionJob:
    return RetentionJob(db, clock, retention_days=7, keep_ledger=keep)


def test_ret01_deletes_after_exactly_seven_days(db: Database, clock: FixedClock) -> None:
    _closed_task(db, clock, closed_ago=timedelta(days=7))
    result = _job(db, clock).run()
    assert result.tasks == 1
    assert result.mail_refs == 1
    assert result.history_cards == 1
    assert result.events == 1
    assert result.ledger_rows == 1
    assert SqliteTaskRepository(db).list_tasks() == []


def test_ret01_keeps_six_days_23_hours(db: Database, clock: FixedClock) -> None:
    task_id = _closed_task(db, clock, closed_ago=timedelta(days=6, hours=23))
    assert _job(db, clock).run().total == 0
    assert SqliteTaskRepository(db).get(task_id) is not None


@pytest.mark.parametrize("status", [TaskStatus.IN_PROGRESS, TaskStatus.WAITING, TaskStatus.ON_HOLD])
def test_ret01_open_tasks_never_deleted(
    db: Database, clock: FixedClock, status: TaskStatus
) -> None:
    old = clock.now() - timedelta(days=60)
    SqliteTaskRepository(db).add(Task("진행 업무", status, TaskSource.MANUAL, old))
    _job(db, clock).run()
    assert len(SqliteTaskRepository(db).list_tasks()) == 1


def test_ret04_deleted_even_if_registered_to_gk(db: Database, clock: FixedClock) -> None:
    from dataclasses import replace

    repo = SqliteTaskRepository(db)
    task_id = _closed_task(db, clock, closed_ago=timedelta(days=8))
    task = repo.get(task_id)
    assert task is not None
    repo.update(replace(task, gk_link="https://gk.example.invalid/k/1"))
    _job(db, clock).run()
    assert repo.get(task_id) is None


def test_ledger_kept_when_flag_true(db: Database, clock: FixedClock) -> None:
    _closed_task(db, clock, closed_ago=timedelta(days=30))
    result = _job(db, clock, keep=True).run()
    assert result.tasks == 1
    assert result.ledger_rows == 0
    assert len(SqliteLedgerRepository(db).list_entries()) == 1


def test_ledger_deleted_when_flag_false(db: Database, clock: FixedClock) -> None:
    _closed_task(db, clock, closed_ago=timedelta(days=30))
    _job(db, clock, keep=False).run()
    assert SqliteLedgerRepository(db).list_entries() == []


def test_orphan_mail_refs_expire(db: Database, clock: FixedClock) -> None:
    refs = SqliteMailRefRepository(db)
    refs.add(MailRef(MailSource.OUTLOOK, "E1", "<old@x.invalid>", "T1", clock.now() - timedelta(8)))
    refs.add(MailRef(MailSource.OUTLOOK, "E2", "<new@x.invalid>", "T2", clock.now()))
    assert _job(db, clock).run().mail_refs == 1
    assert refs.find_by_message_id("<new@x.invalid>") is not None


def test_purge_logs_counts_only(
    db: Database, clock: FixedClock, caplog: pytest.LogCaptureFixture
) -> None:
    _closed_task(db, clock, closed_ago=timedelta(days=9))
    with caplog.at_level(logging.INFO):
        _job(db, clock).run()
    text = caplog.text
    assert "업무 1" in text
    assert "완료 업무" not in text  # 제목 미기록
    assert "x.invalid" not in text


def test_invalid_retention_rejected(db: Database, clock: FixedClock) -> None:
    with pytest.raises(ValueError):
        RetentionJob(db, clock, retention_days=0, keep_ledger=False)


def test_periodic_job_runs_at_start_and_repeats() -> None:
    calls: list[int] = []

    async def scenario() -> int:
        job = PeriodicJob("retention", lambda: calls.append(1), interval_seconds=0.01)
        job.start()
        await asyncio.sleep(0.1)
        await job.stop()
        return job.runs

    runs = asyncio.run(scenario())
    assert runs >= 2
    assert len(calls) >= 2


def test_periodic_job_survives_failure() -> None:
    def boom() -> None:
        raise RuntimeError("x")

    async def scenario() -> int:
        job = PeriodicJob("x", boom, interval_seconds=0.01)
        job.start()
        await asyncio.sleep(0.05)
        await job.stop()
        return job.runs

    assert asyncio.run(scenario()) >= 2
