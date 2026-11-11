"""스키마에 메일 본문·첨부·자격증명 컬럼이 없는지 검사(MAIL-08, AUTH-03, 기획서 4.9)."""

from __future__ import annotations

import re

from storage.db import Database

FORBIDDEN_COLUMN = re.compile(
    r"body|content|html|attach|password|passwd|pwd|secret|token|cookie|session|credential"
    r"|login|user_?id|username|sender|recipient|email|address|phone|rrn",
    re.IGNORECASE,
)


def test_mail08_auth03_no_forbidden_columns(db: Database) -> None:
    offenders = [
        f"{table}.{column}"
        for table, columns in db.table_columns().items()
        for column in columns
        if FORBIDDEN_COLUMN.search(column)
    ]
    assert offenders == []


def test_metric_ledger_has_no_title_or_counterparty(db: Database) -> None:
    columns = set(db.table_columns()["metric_ledger"])
    assert columns == {
        "id",
        "task_id",
        "completed_at",
        "assignee",
        "has_due",
        "due_met",
        "gk_registered",
        "resolution_stage",
    }


def test_secure_delete_enabled(db: Database) -> None:
    assert db.query("PRAGMA secure_delete")[0][0] == 1
    assert db.query("PRAGMA foreign_keys")[0][0] == 1
