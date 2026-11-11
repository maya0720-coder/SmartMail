"""로컬 SQLite DB 연결·스키마·전체 삭제.

- 모든 쿼리는 매개변수화한다(문자열 결합 금지).
- 메일 본문·첨부·자격증명 컬럼은 두지 않는다(MAIL-08, AUTH-03).
  `tests/security`가 스키마를 검사한다.
- `secure_delete`를 켜서 삭제한 행의 내용을 0으로 덮어쓴다. 삭제 후 VACUUM으로 빈 페이지도 정리한다.
- 저장 시 암호화는 적용하지 않는다(결정기록 0004).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 1

# 사용자 데이터 테이블(전체 삭제 대상). 순서는 외래 키 의존의 역순.
DATA_TABLES: Final = (
    "task_event",
    "history_card",
    "mail_ref",
    "weekly_report",
    "ai_call_log",
    "metric_ledger",
    "task",
)

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS task (
    -- AUTOINCREMENT: 삭제된 업무 ID를 재사용하지 않는다(지표 원장·메모리 캐시 혼동 방지)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    requester_dept TEXT CHECK (requester_dept IS NULL OR length(requester_dept) <= 100),
    requested_at TEXT,
    due_at TEXT,
    status TEXT NOT NULL,
    module_tag TEXT CHECK (module_tag IS NULL OR length(module_tag) <= 20),
    source TEXT NOT NULL,
    thread_id TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    gk_link TEXT,
    gk_registered_at TEXT,
    shared INTEGER NOT NULL DEFAULT 0,
    -- 종결(완료·무시) 업무는 종결 시각이 있어야 보관기간 삭제 대상이 된다.
    CHECK (status NOT IN ('done', 'dismissed') OR closed_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_task_thread ON task(thread_id);
CREATE INDEX IF NOT EXISTS ix_task_closed ON task(status, closed_at);

CREATE TABLE IF NOT EXISTS mail_ref (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    summary TEXT CHECK (summary IS NULL OR length(summary) <= 500),
    task_id INTEGER REFERENCES task(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mail_ref_message ON mail_ref(message_id);

CREATE TABLE IF NOT EXISTS task_event (
    id INTEGER PRIMARY KEY,
    task_id INTEGER REFERENCES task(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    detail TEXT CHECK (detail IS NULL OR length(detail) <= 40),
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_event_time ON task_event(occurred_at);

CREATE TABLE IF NOT EXISTS history_card (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL UNIQUE REFERENCES task(id) ON DELETE CASCADE,
    problem TEXT NOT NULL CHECK (length(problem) <= 2000),
    cause TEXT NOT NULL CHECK (length(cause) <= 2000),
    action TEXT NOT NULL CHECK (length(action) <= 2000),
    tcode TEXT CHECK (tcode IS NULL OR length(tcode) <= 100),
    tags TEXT NOT NULL DEFAULT '' CHECK (length(tags) <= 300),
    visibility TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metric_ledger (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL UNIQUE,
    completed_at TEXT NOT NULL,
    assignee TEXT NOT NULL,
    has_due INTEGER NOT NULL,
    due_met INTEGER,
    gk_registered INTEGER NOT NULL DEFAULT 0,
    resolution_stage TEXT
);

CREATE TABLE IF NOT EXISTS weekly_report (
    id INTEGER PRIMARY KEY,
    week_start TEXT NOT NULL,
    page_id TEXT,
    page_version INTEGER,
    page_link TEXT,
    approved_at TEXT,
    published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_call_log (
    id INTEGER PRIMARY KEY,
    called_at TEXT NOT NULL,
    request_summary TEXT NOT NULL CHECK (length(request_summary) <= 300),
    success INTEGER NOT NULL
);
"""


def to_db_time(value: datetime | None) -> str | None:
    """aware 시각을 UTC ISO 문자열로 바꾼다(문자열 비교로 시간 순서가 유지된다)."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("시간대가 있는 시각이 필요합니다")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def from_db_time(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class Database:
    """SQLite 연결 하나를 감싼다. 여러 스레드에서 쓰면 잠금으로 직렬화한다."""

    def __init__(self, path: Path | str) -> None:
        self._path = path
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA secure_delete = ON")
        # 정렬·VACUUM 임시 데이터를 디스크 임시 파일에 남기지 않는다.
        self._conn.execute("PRAGMA temp_store = MEMORY")
        self._conn.executescript(SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION:d}")

    @classmethod
    def in_memory(cls) -> Database:
        return cls(":memory:")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """쓰기 트랜잭션. 예외가 나면 되돌린다."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")

    def table_columns(self) -> dict[str, list[str]]:
        """테이블별 컬럼 이름(스키마 검사용)."""
        tables = [
            row["name"]
            for row in self.query("SELECT name FROM sqlite_master WHERE type = 'table'")
            if not str(row["name"]).startswith("sqlite_")
        ]
        result: dict[str, list[str]] = {}
        for table in tables:
            # 테이블 이름은 sqlite_master에서 읽은 값이라 사용자 입력이 아니다.
            rows = self.query(f"PRAGMA table_info({table})")  # noqa: S608
            result[table] = [str(row["name"]) for row in rows]
        return result

    def wipe_all(self) -> int:
        """사용자 요청에 따른 로컬 데이터 전체 삭제. 삭제한 행 수를 돌려준다."""
        total = 0
        with self.transaction() as conn:
            for table in DATA_TABLES:
                # 테이블 이름은 위 상수 목록에서만 온다.
                total += conn.execute(f"DELETE FROM {table}").rowcount  # noqa: S608
        self.vacuum()
        return total

    def close(self) -> None:
        with self._lock:
            self._conn.close()
