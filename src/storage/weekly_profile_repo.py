"""주간보고 양식 프로필 저장소(결정기록 0037).

참고 링크마다 1개. 양식 구조(블록 JSON)·칸 연결·구조 지문·가져온 시각만 둔다.
링크를 지우면 함께 지워진다(외래 키 ON DELETE CASCADE). 전체 삭제 대상이다.
"""

from __future__ import annotations

from domain.weekly.profile_service import StoredProfile
from storage.db import Database, from_db_time, to_db_time


def _row(row: object) -> StoredProfile:
    r: dict[str, object] = dict(row)  # type: ignore[call-overload]
    fetched = from_db_time(str(r["fetched_at"]))
    assert fetched is not None  # noqa: S101 - NOT NULL 열
    return StoredProfile(
        link_id=int(str(r["link_id"])),
        name=str(r["name"]),
        blocks_json=str(r["blocks_json"]),
        bindings_json=str(r["bindings_json"]),
        source_hash=str(r["source_hash"]),
        source=str(r["source"]),
        fetched_at=fetched,
    )


class SqliteWeeklyProfiles:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, profile: StoredProfile) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO weekly_template_profile"
                " (link_id, name, blocks_json, bindings_json, source_hash, source, fetched_at)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(link_id) DO UPDATE SET name = excluded.name,"
                " blocks_json = excluded.blocks_json, bindings_json = excluded.bindings_json,"
                " source_hash = excluded.source_hash, source = excluded.source,"
                " fetched_at = excluded.fetched_at",
                (
                    profile.link_id,
                    profile.name,
                    profile.blocks_json,
                    profile.bindings_json,
                    profile.source_hash,
                    profile.source,
                    to_db_time(profile.fetched_at),
                ),
            )

    def update_bindings(self, link_id: int, bindings_json: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE weekly_template_profile SET bindings_json = ? WHERE link_id = ?",
                (bindings_json, link_id),
            )
            return cursor.rowcount > 0

    def get(self, link_id: int) -> StoredProfile | None:
        rows = self._db.query(
            "SELECT link_id, name, blocks_json, bindings_json, source_hash, source, fetched_at"
            " FROM weekly_template_profile WHERE link_id = ?",
            (link_id,),
        )
        return _row(rows[0]) if rows else None

    def linked_ids(self) -> set[int]:
        return {
            int(r["link_id"]) for r in self._db.query("SELECT link_id FROM weekly_template_profile")
        }

    def delete(self, link_id: int) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM weekly_template_profile WHERE link_id = ?", (link_id,)
            )
            return cursor.rowcount > 0
