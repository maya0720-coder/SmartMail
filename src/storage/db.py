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

SCHEMA_VERSION: Final = 6  # 2: 트리아지(0015), 3: 구조화 요약(0021), 4: 0027~0030, 5: 0035, 6: 0037

# 사용자 데이터 테이블(전체 삭제 대상). 순서는 외래 키 의존의 역순.
DATA_TABLES: Final = (
    "task_event",
    "history_card",
    "mail_ref",
    "weekly_report",
    "weekly_template_profile",
    "weekly_link",
    "weekly_publish_cache",
    "ai_call_log",
    "metric_ledger",
    "task",
    "sync_state",
    "app_setting",
    "triage_rules",
    "domain_company",
    "weekly_draft",
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

-- 주간보고 참고 링크(결정기록 0024): https 주소·이름·양식 목차(머리글 줄)만. 개인정보 없음.
CREATE TABLE IF NOT EXISTS weekly_link (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL CHECK (length(url) <= 500),
    name TEXT NOT NULL CHECK (length(name) <= 120),
    platform TEXT NOT NULL CHECK (platform IN ('slack', 'wiki', 'web')),
    outline TEXT NOT NULL DEFAULT '' CHECK (length(outline) <= 3000)
);

-- 주간보고 게시 캐시(결정기록 0024): 마스킹된 본문 스냅샷. 보관 기간이 지나면 삭제한다.
CREATE TABLE IF NOT EXISTS weekly_publish_cache (
    id INTEGER PRIMARY KEY,
    week_start TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('slack', 'wiki', 'web')),
    method TEXT NOT NULL CHECK (method IN ('api', 'clipboard')),
    link TEXT CHECK (link IS NULL OR length(link) <= 2000),
    published_at TEXT NOT NULL,
    snapshot TEXT NOT NULL CHECK (length(snapshot) <= 60000)
);

CREATE TABLE IF NOT EXISTS ai_call_log (
    id INTEGER PRIMARY KEY,
    called_at TEXT NOT NULL,
    request_summary TEXT NOT NULL CHECK (length(request_summary) <= 300),
    success INTEGER NOT NULL
);

-- 동기화 기준 시각(예: 메일 자동 수집 마지막 성공 시각). 값은 시각 문자열만 둔다(MAIL-04).
CREATE TABLE IF NOT EXISTS sync_state (
    name TEXT PRIMARY KEY CHECK (length(name) <= 40),
    synced_at TEXT NOT NULL
);

-- 화면에서 바꾼 앱 설정(예: 위키 제목 규칙). 개인정보·자격증명은 넣지 않는다.
-- 저장 가능한 이름은 저장소 코드의 허용 목록으로 제한한다.
CREATE TABLE IF NOT EXISTS app_setting (
    name TEXT PRIMARY KEY CHECK (length(name) <= 40),
    value TEXT NOT NULL CHECK (length(value) <= 200)
);
"""


# 스키마 2(결정기록 0015): 제안 트리아지 필드. 이유는 코드값 목록만(문장 저장 금지).
MIGRATION_2_TASK_COLUMNS: Final = (
    ("snooze_until", "TEXT"),
    ("confidence", "REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))"),
    ("reasons", "TEXT CHECK (reasons IS NULL OR length(reasons) <= 200)"),
    (
        "weekly_section",
        "TEXT CHECK (weekly_section IS NULL OR weekly_section IN"
        " ('done', 'planned', 'issues', 'none'))",
    ),
)
# '업무 아님' 학습 규칙: 원문 주소·말머리 대신 HMAC 키 해시만 저장(3단계에서 사용)
MIGRATION_2_TRIAGE_RULES: Final = """
CREATE TABLE IF NOT EXISTS triage_rules (
    key_hash TEXT PRIMARY KEY CHECK (length(key_hash) = 64),
    kind TEXT NOT NULL CHECK (kind IN ('sender', 'prefix')),
    dismiss_count INTEGER NOT NULL DEFAULT 0 CHECK (dismiss_count >= 0),
    updated_at TEXT NOT NULL
)
"""


# 스키마 3(결정기록 0021): 메일 참조의 구조화 요약(마스킹된 AI 요약만, 원문 아님)과 요약 시도 횟수
MIGRATION_3_MAIL_REF_COLUMNS: Final = (
    ("digest", "TEXT CHECK (digest IS NULL OR length(digest) <= 1500)"),
    ("summary_tries", "INTEGER NOT NULL DEFAULT 0 CHECK (summary_tries >= 0)"),
)


# 스키마 4(결정기록 0027~0029): 요약 상태·메일 방향, 스레드 최종 메일·진행 요약, 외부 회사 도메인.
# 개인정보 아님: 방향은 받음/보냄 코드, 도메인은 회사 도메인(개인 메일 도메인은 저장하지 않음).
SUMMARY_STATES: Final = ("pending", "done", "kept", "missing")
MIGRATION_4_MAIL_REF_COLUMNS: Final = (
    (
        "summary_state",
        "TEXT CHECK (summary_state IS NULL OR summary_state IN"
        " ('pending', 'done', 'kept', 'missing'))",
    ),
    ("direction", "TEXT CHECK (direction IS NULL OR direction IN ('in', 'out'))"),
)
MIGRATION_4_TASK_COLUMNS: Final = (
    ("last_mail_at", "TEXT"),
    (
        "last_mail_direction",
        "TEXT CHECK (last_mail_direction IS NULL OR last_mail_direction IN ('in', 'out'))",
    ),
    ("progress_digest", "TEXT CHECK (progress_digest IS NULL OR length(progress_digest) <= 1000)"),
    ("progress_ref_count", "INTEGER CHECK (progress_ref_count IS NULL OR progress_ref_count >= 0)"),
    (
        "requester_domain",
        "TEXT CHECK (requester_domain IS NULL OR length(requester_domain) <= 253)",
    ),
    (
        "requester_kind",
        "TEXT CHECK (requester_kind IS NULL OR requester_kind IN"
        " ('internal', 'external', 'freemail'))",
    ),
)
# G-Knowledge 백그라운드 등록 상태(결정기록 0035). reason은 코드 값(단계·실패 사유)만 담는다
GK_STATES: Final = ("queued", "running", "requested", "failed_unsent", "unknown")
MIGRATION_5_TASK_COLUMNS: Final = (
    (
        "gk_state",
        "TEXT CHECK (gk_state IS NULL OR gk_state IN"
        " ('queued', 'running', 'requested', 'failed_unsent', 'unknown'))",
    ),
    ("gk_state_at", "TEXT"),
    (
        "gk_state_reason",
        "TEXT CHECK (gk_state_reason IS NULL OR (length(gk_state_reason) <= 32"
        " AND gk_state_reason NOT GLOB '*[^a-z_]*'))",
    ),
)
MIGRATION_4_DOMAIN_COMPANY: Final = """
CREATE TABLE IF NOT EXISTS domain_company (
    domain TEXT PRIMARY KEY CHECK (length(domain) BETWEEN 1 AND 253),
    company TEXT NOT NULL CHECK (length(company) BETWEEN 1 AND 50),
    source TEXT NOT NULL CHECK (source IN ('rule', 'ai', 'user')),
    resolved_at TEXT NOT NULL
)
"""
# 주간보고 편집기 임시 저장(결정기록 0030): 정리·마스킹된 HTML 사본, 주차별 1개. 보관 기간 후 삭제
MIGRATION_4_WEEKLY_DRAFT: Final = """
CREATE TABLE IF NOT EXISTS weekly_draft (
    week_start TEXT PRIMARY KEY,
    snapshot TEXT NOT NULL CHECK (length(snapshot) <= 200000),
    saved_at TEXT NOT NULL
)
"""
# 주간보고 양식 프로필(결정기록 0037): 참고 링크마다 1개.
# 양식 구조(머리글·열 이름·고정 문구, 마스킹)와 칸 연결만.
# 표 데이터 행 글자·다른 팀원 영역·이름은 저장하지 않는다
MIGRATION_6_WEEKLY_PROFILE: Final = """
CREATE TABLE IF NOT EXISTS weekly_template_profile (
    id INTEGER PRIMARY KEY,
    link_id INTEGER NOT NULL UNIQUE REFERENCES weekly_link(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(name) <= 120),
    blocks_json TEXT NOT NULL CHECK (length(blocks_json) <= 65536),
    bindings_json TEXT NOT NULL CHECK (length(bindings_json) <= 16384),
    source_hash TEXT NOT NULL CHECK (length(source_hash) = 64),
    source TEXT NOT NULL CHECK (source IN ('wiki', 'paste')),
    fetched_at TEXT NOT NULL
)
"""
# 기존 메일 참조의 요약 상태를 1회 계산한다(결정기록 0029). 이후에는 요약 작업이 기록한다.
MIGRATION_4_SUMMARY_STATE: Final = """
UPDATE mail_ref SET summary_state = CASE
    WHEN summary IS NULL THEN 'pending'
    WHEN digest IS NOT NULL
         AND summary = (SELECT title FROM task WHERE task.id = mail_ref.task_id) THEN 'done'
    ELSE 'kept' END
WHERE summary_state IS NULL
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
        self._migrate()
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION:d}")

    def _migrate(self) -> None:
        """스키마 1 → 2 → 3 → 4: 기존 데이터를 보존하며 열을 더한다(새 DB도 같은 경로).

        여러 번 실행해도 안전하다(이미 있는 열은 건너뜀).
        """

        def columns(table: str) -> set[str]:
            return {str(r["name"]) for r in self._conn.execute(f"PRAGMA table_info({table})")}

        existing = {"task": columns("task"), "mail_ref": columns("mail_ref")}
        plan = [
            ("task", MIGRATION_2_TASK_COLUMNS),
            ("mail_ref", MIGRATION_3_MAIL_REF_COLUMNS),
            ("mail_ref", MIGRATION_4_MAIL_REF_COLUMNS),
            ("task", MIGRATION_4_TASK_COLUMNS),
            ("task", MIGRATION_5_TASK_COLUMNS),
        ]
        with self.transaction() as conn:
            for table, adds in plan:
                for name, ddl in adds:
                    if name not in existing[table]:
                        # 표·열 이름·정의는 위 상수에서만 온다(사용자 입력 아님)
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")  # noqa: S608
            conn.execute(MIGRATION_2_TRIAGE_RULES)
            conn.execute(MIGRATION_4_DOMAIN_COMPANY)
            conn.execute(MIGRATION_4_WEEKLY_DRAFT)
            conn.execute(MIGRATION_6_WEEKLY_PROFILE)
            if "summary_state" not in existing["mail_ref"]:
                conn.execute(MIGRATION_4_SUMMARY_STATE)

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
