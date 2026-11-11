"""히스토리 카드·지표 원장·AI 호출 이력·주간보고 게시 이력 저장소(SQLite)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime

from domain.models import HistoryCard, LedgerEntry, ResolutionStage, Visibility
from domain.weekly.service import WikiPage
from security.masking import MaskedText, mask, require_masked
from storage.db import Database, from_db_time, to_db_time

TAG_SEPARATOR = ","


def _time(value: str) -> datetime:
    result = from_db_time(value)
    assert result is not None  # noqa: S101 - NOT NULL 컬럼
    return result


def _card_from_row(row: sqlite3.Row) -> HistoryCard:
    tags = tuple(t for t in str(row["tags"]).split(TAG_SEPARATOR) if t)
    return HistoryCard(
        id=row["id"],
        task_id=row["task_id"],
        problem=row["problem"],
        cause=row["cause"],
        action=row["action"],
        tcode=row["tcode"],
        tags=tags,
        visibility=Visibility(row["visibility"]),
        created_at=_time(row["created_at"]),
    )


class SqliteHistoryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, card: HistoryCard) -> HistoryCard:
        if any(TAG_SEPARATOR in tag for tag in card.tags):
            raise ValueError("태그에 쉼표를 넣을 수 없습니다")
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO history_card (task_id, problem, cause, action, tcode, tags,"
                " visibility, created_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(task_id) DO UPDATE SET problem=excluded.problem,"
                " cause=excluded.cause, action=excluded.action, tcode=excluded.tcode,"
                " tags=excluded.tags, visibility=excluded.visibility",
                (
                    card.task_id,
                    card.problem,
                    card.cause,
                    card.action,
                    card.tcode,
                    TAG_SEPARATOR.join(card.tags),
                    card.visibility.value,
                    to_db_time(card.created_at),
                ),
            )
        return replace(card, id=cursor.lastrowid)

    def get_for_task(self, task_id: int) -> HistoryCard | None:
        rows = self._db.query("SELECT * FROM history_card WHERE task_id = ?", (task_id,))
        return _card_from_row(rows[0]) if rows else None

    def list_cards(self) -> list[HistoryCard]:
        rows = self._db.query("SELECT * FROM history_card ORDER BY created_at DESC")
        return [_card_from_row(row) for row in rows]


def _bool_or_none(value: int | None) -> bool | None:
    return None if value is None else bool(value)


class SqliteLedgerRepository:
    """지표 원장. 업무 1건 = 숫자 1행(제목·본문·상대방 정보 없음)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, entry: LedgerEntry) -> LedgerEntry:
        stage = entry.resolution_stage.value if entry.resolution_stage else None
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO metric_ledger (task_id, completed_at, assignee, has_due, due_met,"
                " gk_registered, resolution_stage) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(task_id) DO UPDATE SET completed_at=excluded.completed_at,"
                " assignee=excluded.assignee, has_due=excluded.has_due,"
                " due_met=excluded.due_met,"
                " gk_registered=MAX(gk_registered, excluded.gk_registered),"
                " resolution_stage=COALESCE(excluded.resolution_stage, resolution_stage)",
                (
                    entry.task_id,
                    to_db_time(entry.completed_at),
                    entry.assignee,
                    int(entry.has_due),
                    None if entry.due_met is None else int(entry.due_met),
                    int(entry.gk_registered),
                    stage,
                ),
            )
        rows = self._db.query("SELECT id FROM metric_ledger WHERE task_id = ?", (entry.task_id,))
        return replace(entry, id=rows[0]["id"])

    def mark_gk_registered(self, task_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute("UPDATE metric_ledger SET gk_registered = 1 WHERE task_id = ?", (task_id,))

    def set_resolution(self, task_id: int, stage: ResolutionStage) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE metric_ledger SET resolution_stage = ? WHERE task_id = ?",
                (stage.value, task_id),
            )

    def list_entries(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[LedgerEntry]:
        rows = self._db.query(
            "SELECT * FROM metric_ledger WHERE (? IS NULL OR completed_at >= ?)"
            " AND (? IS NULL OR completed_at < ?) ORDER BY completed_at",
            (to_db_time(since), to_db_time(since), to_db_time(until), to_db_time(until)),
        )
        return [
            LedgerEntry(
                id=row["id"],
                task_id=row["task_id"],
                completed_at=_time(row["completed_at"]),
                assignee=row["assignee"],
                has_due=bool(row["has_due"]),
                due_met=_bool_or_none(row["due_met"]),
                gk_registered=bool(row["gk_registered"]),
                resolution_stage=(
                    ResolutionStage(row["resolution_stage"]) if row["resolution_stage"] else None
                ),
            )
            for row in rows
        ]


@dataclass(frozen=True, slots=True)
class AiCallRecord:
    called_at: datetime
    request_summary: MaskedText  # 마스킹된 요약만(타입으로 강제)
    success: bool


class SqliteAiCallLog:
    """g-llm 호출 이력(AI-06). 요약은 마스킹된 값만 받는다."""

    MAX_SUMMARY_CHARS = 300

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, record: AiCallRecord) -> None:
        summary = require_masked(record.request_summary).value[: self.MAX_SUMMARY_CHARS]
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO ai_call_log (called_at, request_summary, success) VALUES (?,?,?)",
                (to_db_time(record.called_at), summary, int(record.success)),
            )

    def list_recent(self, limit: int = 50) -> list[AiCallRecord]:
        rows = self._db.query("SELECT * FROM ai_call_log ORDER BY called_at DESC LIMIT ?", (limit,))
        return [
            # DB에 저장된 값은 저장 전에 이미 마스킹된 값이며, 다시 마스킹해도 결과가 같다.
            AiCallRecord(_time(r["called_at"]), mask(r["request_summary"]), bool(r["success"]))
            for r in rows
        ]


@dataclass(frozen=True, slots=True)
class PublishRecord:
    week_start: datetime
    published_at: datetime
    page_id: str | None = None
    page_version: int | None = None
    page_link: str | None = None
    approved_at: datetime | None = None


class SqlitePublishLog:
    """주간보고 게시 이력·버전 링크(WIKI-06). 본문은 저장하지 않는다."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, record: PublishRecord) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO weekly_report (week_start, page_id, page_version, page_link,"
                " approved_at, published_at) VALUES (?,?,?,?,?,?)",
                (
                    to_db_time(record.week_start),
                    record.page_id,
                    record.page_version,
                    record.page_link,
                    to_db_time(record.approved_at),
                    to_db_time(record.published_at),
                ),
            )

    def record(
        self,
        week_start: datetime,
        page: WikiPage,
        approved_at: datetime,
        published_at: datetime,
    ) -> None:
        """WeeklyService의 PublishLog 인터페이스 구현."""
        self.add(
            PublishRecord(
                week_start=week_start,
                published_at=published_at,
                page_id=page.page_id,
                page_version=page.version,
                page_link=page.link,
                approved_at=approved_at,
            )
        )

    def list_recent(self, limit: int = 20) -> list[PublishRecord]:
        rows = self._db.query(
            "SELECT * FROM weekly_report ORDER BY published_at DESC LIMIT ?", (limit,)
        )
        return [
            PublishRecord(
                week_start=_time(r["week_start"]),
                published_at=_time(r["published_at"]),
                page_id=r["page_id"],
                page_version=r["page_version"],
                page_link=r["page_link"],
                approved_at=from_db_time(r["approved_at"]),
            )
            for r in rows
        ]
