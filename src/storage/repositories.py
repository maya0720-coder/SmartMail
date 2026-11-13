"""`domain.ports` 저장소 인터페이스의 SQLite 구현(업무·메일 참조·이벤트)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from domain.models import (
    MailRef,
    MailSource,
    Task,
    TaskEvent,
    TaskEventType,
    TaskSource,
    TaskStatus,
)
from domain.ports import Clock
from storage.db import Database, from_db_time, to_db_time


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        status=TaskStatus(row["status"]),
        source=TaskSource(row["source"]),
        created_at=_required_time(row["created_at"]),
        requester_dept=row["requester_dept"],
        requested_at=from_db_time(row["requested_at"]),
        due_at=from_db_time(row["due_at"]),
        module_tag=row["module_tag"],
        thread_id=row["thread_id"],
        closed_at=from_db_time(row["closed_at"]),
        gk_link=row["gk_link"],
        gk_registered_at=from_db_time(row["gk_registered_at"]),
        shared=bool(row["shared"]),
        snooze_until=from_db_time(row["snooze_until"]),
        confidence=row["confidence"],
        reasons=tuple(code for code in (row["reasons"] or "").split(",") if code),
        weekly_section=row["weekly_section"],
    )


def _required_time(value: str) -> datetime:
    result = from_db_time(value)
    assert result is not None  # noqa: S101 - NOT NULL 컬럼
    return result


def _task_params(task: Task) -> tuple[object, ...]:
    return (
        task.title,
        task.requester_dept,
        to_db_time(task.requested_at),
        to_db_time(task.due_at),
        task.status.value,
        task.module_tag,
        task.source.value,
        task.thread_id,
        to_db_time(task.created_at),
        to_db_time(task.closed_at),
        task.gk_link,
        to_db_time(task.gk_registered_at),
        int(task.shared),
        to_db_time(task.snooze_until),
        task.confidence,
        ",".join(task.reasons) or None,
        task.weekly_section,
    )


class SqliteTaskRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, task: Task) -> Task:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO task (title, requester_dept, requested_at, due_at, status,"
                " module_tag, source, thread_id, created_at, closed_at, gk_link,"
                " gk_registered_at, shared, snooze_until, confidence, reasons, weekly_section)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                _task_params(task),
            )
        return replace(task, id=cursor.lastrowid)

    def get(self, task_id: int) -> Task | None:
        rows = self._db.query("SELECT * FROM task WHERE id = ?", (task_id,))
        return _task_from_row(rows[0]) if rows else None

    def update(self, task: Task) -> Task:
        if task.id is None:
            raise ValueError("저장되지 않은 업무입니다")
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE task SET title=?, requester_dept=?, requested_at=?, due_at=?, status=?,"
                " module_tag=?, source=?, thread_id=?, created_at=?, closed_at=?, gk_link=?,"
                " gk_registered_at=?, shared=?, snooze_until=?, confidence=?, reasons=?,"
                " weekly_section=? WHERE id=?",
                (*_task_params(task), task.id),
            )
        if cursor.rowcount != 1:
            raise LookupError("업무를 찾을 수 없습니다")
        return task

    def find_by_thread(self, thread_id: str) -> Task | None:
        rows = self._db.query(
            "SELECT * FROM task WHERE thread_id = ? ORDER BY id LIMIT 1", (thread_id,)
        )
        return _task_from_row(rows[0]) if rows else None

    def list_tasks(self, statuses: Sequence[TaskStatus] | None = None) -> list[Task]:
        if statuses is None:
            rows = self._db.query("SELECT * FROM task ORDER BY id")
        else:
            marks = ",".join("?" for _ in statuses)
            rows = self._db.query(
                f"SELECT * FROM task WHERE status IN ({marks}) ORDER BY id",  # noqa: S608
                tuple(s.value for s in statuses),
            )
        return [_task_from_row(row) for row in rows]


def _mail_from_row(row: sqlite3.Row) -> MailRef:
    return MailRef(
        id=row["id"],
        source=MailSource(row["source"]),
        entry_id=row["entry_id"],
        message_id=row["message_id"],
        thread_id=row["thread_id"],
        received_at=_required_time(row["received_at"]),
        summary=row["summary"],
        task_id=row["task_id"],
    )


class SqliteSyncState:
    """동기화 기준 시각 저장소(MAIL-04 자동 수집 커서)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, name: str) -> datetime | None:
        rows = self._db.query("SELECT synced_at FROM sync_state WHERE name = ?", (name,))
        return from_db_time(rows[0]["synced_at"]) if rows else None

    def set(self, name: str, synced_at: datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO sync_state (name, synced_at) VALUES (?, ?)"
                " ON CONFLICT(name) DO UPDATE SET synced_at = excluded.synced_at",
                (name, to_db_time(synced_at)),
            )


class SqliteAppSettings:
    """화면에서 바꾼 앱 설정. 허용된 이름만 저장한다(자격증명·개인정보 저장 방지)."""

    ALLOWED = frozenset(
        {
            "wiki_title_format",
            "wiki_title_offset_days",
            "task_categories",
            # 화면 패널 폭(px 숫자만, 결정기록 0016)
            "ui_detail_w",
            "ui_triage_list_w",
        }
    )

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, name: str) -> str | None:
        rows = self._db.query("SELECT value FROM app_setting WHERE name = ?", (name,))
        return str(rows[0]["value"]) if rows else None

    def set(self, name: str, value: str) -> None:
        if name not in self.ALLOWED:
            raise ValueError("허용되지 않은 설정 이름입니다")
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_setting (name, value) VALUES (?, ?)"
                " ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (name, value),
            )

    def delete(self, name: str) -> None:
        if name not in self.ALLOWED:
            raise ValueError("허용되지 않은 설정 이름입니다")
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM app_setting WHERE name = ?", (name,))


class SqliteMailRefRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, ref: MailRef) -> MailRef:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO mail_ref (source, entry_id, message_id, thread_id, received_at,"
                " summary, task_id) VALUES (?,?,?,?,?,?,?)",
                (
                    ref.source.value,
                    ref.entry_id,
                    ref.message_id,
                    ref.thread_id,
                    to_db_time(ref.received_at),
                    ref.summary,
                    ref.task_id,
                ),
            )
        return replace(ref, id=cursor.lastrowid)

    def get(self, ref_id: int) -> MailRef | None:
        rows = self._db.query("SELECT * FROM mail_ref WHERE id = ?", (ref_id,))
        return _mail_from_row(rows[0]) if rows else None

    def find_by_message_id(self, message_id: str) -> MailRef | None:
        rows = self._db.query("SELECT * FROM mail_ref WHERE message_id = ?", (message_id,))
        return _mail_from_row(rows[0]) if rows else None

    def link_task(self, ref_id: int, task_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute("UPDATE mail_ref SET task_id = ? WHERE id = ?", (task_id, ref_id))

    def list_for_task(self, task_id: int) -> list[MailRef]:
        rows = self._db.query(
            "SELECT * FROM mail_ref WHERE task_id = ? ORDER BY received_at", (task_id,)
        )
        return [_mail_from_row(row) for row in rows]

    def set_summary(self, ref_id: int, summary: str | None) -> None:
        with self._db.transaction() as conn:
            conn.execute("UPDATE mail_ref SET summary = ? WHERE id = ?", (summary, ref_id))


class SqliteEventRecorder:
    """업무 이벤트 기록(성과 지표 원천). detail에는 열거형 값만 넣는다."""

    MAX_DETAIL_CHARS = 40

    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def record(
        self, kind: TaskEventType, *, task_id: int | None = None, detail: str | None = None
    ) -> TaskEvent:
        if detail is not None and (
            len(detail) > self.MAX_DETAIL_CHARS or not detail.replace("_", "").isalnum()
        ):
            raise ValueError("이벤트 상세값은 짧은 코드값만 허용합니다")
        event = TaskEvent(kind=kind, occurred_at=self._clock.now(), task_id=task_id, detail=detail)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO task_event (task_id, kind, detail, occurred_at) VALUES (?,?,?,?)",
                (task_id, kind.value, detail, to_db_time(event.occurred_at)),
            )
        return replace(event, id=cursor.lastrowid)

    def list_events(
        self,
        *,
        task_id: int | None = None,
        kinds: Sequence[TaskEventType] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[TaskEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if kinds is not None:
            clauses.append(f"kind IN ({','.join('?' for _ in kinds)})")
            params.extend(k.value for k in kinds)
        if since is not None:
            clauses.append("occurred_at >= ?")
            params.append(to_db_time(since))
        if until is not None:
            clauses.append("occurred_at < ?")
            params.append(to_db_time(until))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._db.query(
            f"SELECT * FROM task_event{where} ORDER BY occurred_at, id",  # noqa: S608
            tuple(params),
        )
        return [
            TaskEvent(
                id=row["id"],
                task_id=row["task_id"],
                kind=TaskEventType(row["kind"]),
                detail=row["detail"],
                occurred_at=_required_time(row["occurred_at"]),
            )
            for row in rows
        ]
