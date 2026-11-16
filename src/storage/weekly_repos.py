"""주간보고 참고 링크·게시 캐시 저장소(WIKI-01, WIKI-06).

- 참고 링크: 사용자가 등록한 https 주소·이름·양식 목차(머리글 줄). 메일 원문·개인정보 없음.
- 게시 캐시: 주차·플랫폼·방식·시각·링크와 마스킹된 본문 스냅샷.
  보관기간이 지나면 RetentionJob이 지운다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.clock import KST
from domain.weekly.links import Platform, ReferenceLink
from storage.db import Database, from_db_time, to_db_time

MAX_SNAPSHOT_CHARS = 60_000  # 테이블 CHECK 제약과 같은 값


def _link(row: object) -> ReferenceLink:
    r: dict[str, object] = dict(row)  # type: ignore[call-overload]
    return ReferenceLink(
        id=int(str(r["id"])),
        url=str(r["url"]),
        name=str(r["name"]),
        platform=Platform(str(r["platform"])),
        outline=str(r["outline"]),
    )


class SqliteReferenceLinks:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, url: str, name: str, platform: str, outline: str) -> ReferenceLink:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO weekly_link (url, name, platform, outline) VALUES (?,?,?,?)",
                (url, name, platform, outline),
            )
        return ReferenceLink(int(cursor.lastrowid or 0), url, name, Platform(platform), outline)

    def list_all(self) -> list[ReferenceLink]:
        return [_link(r) for r in self._db.query("SELECT * FROM weekly_link ORDER BY id")]

    def get(self, link_id: int) -> ReferenceLink | None:
        rows = self._db.query("SELECT * FROM weekly_link WHERE id = ?", (link_id,))
        return _link(rows[0]) if rows else None

    def delete(self, link_id: int) -> bool:
        with self._db.transaction() as conn:
            return conn.execute("DELETE FROM weekly_link WHERE id = ?", (link_id,)).rowcount > 0


@dataclass(frozen=True, slots=True)
class CacheEntry:
    id: int
    week_start: datetime
    platform: str
    method: str  # "api" | "clipboard"
    link: str | None
    published_at: datetime
    snapshot: str


def _entry(row: object) -> CacheEntry:
    r: dict[str, object] = dict(row)  # type: ignore[call-overload]
    week = from_db_time(str(r["week_start"]))
    published = from_db_time(str(r["published_at"]))
    link = r["link"]
    return CacheEntry(
        int(str(r["id"])),
        (week or datetime.now(KST)).astimezone(KST),
        str(r["platform"]),
        str(r["method"]),
        None if link is None else str(link),
        (published or datetime.now(KST)).astimezone(KST),
        str(r["snapshot"]),
    )


class SqliteWeeklyCache:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        week_start: datetime,
        platform: str,
        method: str,
        link: str | None,
        published_at: datetime,
        snapshot: str,
    ) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO weekly_publish_cache (week_start, platform, method, link,"
                " published_at, snapshot) VALUES (?,?,?,?,?,?)",
                (
                    to_db_time(week_start),
                    platform,
                    method,
                    link,
                    to_db_time(published_at),
                    snapshot[:MAX_SNAPSHOT_CHARS],
                ),
            )
        return int(cursor.lastrowid or 0)

    def list_recent(self, limit: int = 20) -> list[CacheEntry]:
        rows = self._db.query(
            "SELECT * FROM weekly_publish_cache ORDER BY published_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [_entry(r) for r in rows]

    def get(self, entry_id: int) -> CacheEntry | None:
        rows = self._db.query("SELECT * FROM weekly_publish_cache WHERE id = ?", (entry_id,))
        return _entry(rows[0]) if rows else None


class SqliteWeeklyDrafts:
    """주간보고 편집기 임시 저장(결정기록 0030). 호출자가 정리·마스킹한 HTML만 넘긴다."""

    MAX_HTML_CHARS = 200_000

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, week_start: datetime, html: str, saved_at: datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO weekly_draft (week_start, snapshot, saved_at) VALUES (?, ?, ?)"
                " ON CONFLICT(week_start) DO UPDATE SET snapshot = excluded.snapshot,"
                " saved_at = excluded.saved_at",
                (to_db_time(week_start), html[: self.MAX_HTML_CHARS], to_db_time(saved_at)),
            )

    def get(self, week_start: datetime) -> tuple[str, datetime] | None:
        rows = self._db.query(
            "SELECT snapshot, saved_at FROM weekly_draft WHERE week_start = ?",
            (to_db_time(week_start),),
        )
        if not rows:
            return None
        saved = from_db_time(rows[0]["saved_at"])
        assert saved is not None  # noqa: S101 - NOT NULL 컬럼
        return str(rows[0]["snapshot"]), saved
