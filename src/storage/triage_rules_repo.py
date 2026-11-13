"""'업무 아님' 학습 규칙 저장소(결정기록 0015). 키 해시(64자 16진)와 횟수만 저장한다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from domain.ports import Clock
from storage.db import Database, to_db_time

KINDS: Final = frozenset({"sender", "prefix"})
_HASH = re.compile(r"[0-9a-f]{64}")
MAX_LOOKUP: Final = 20


class SqliteTriageRules:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def counts(self, key_hashes: Sequence[str]) -> dict[str, int]:
        hashes = [h for h in key_hashes[:MAX_LOOKUP] if _HASH.fullmatch(h)]
        if not hashes:
            return {}
        marks = ",".join("?" for _ in hashes)  # 자리표시자만 만든다(값은 매개변수로)
        rows = self._db.query(
            f"SELECT key_hash, dismiss_count FROM triage_rules WHERE key_hash IN ({marks})",  # noqa: S608
            tuple(hashes),
        )
        return {str(r["key_hash"]): int(r["dismiss_count"]) for r in rows}

    def bump(self, kind: str, key_hash: str, delta: int) -> None:
        if kind not in KINDS or not _HASH.fullmatch(key_hash):
            raise ValueError("학습 규칙 키 형식이 올바르지 않습니다")
        now = to_db_time(self._clock.now())
        with self._db.transaction() as conn:
            if delta > 0:
                conn.execute(
                    "INSERT INTO triage_rules (key_hash, kind, dismiss_count, updated_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT(key_hash) DO UPDATE SET"
                    " dismiss_count = dismiss_count + excluded.dismiss_count,"
                    " updated_at = excluded.updated_at",
                    (key_hash, kind, delta, now),
                )
                return
            conn.execute(
                "UPDATE triage_rules SET dismiss_count = max(0, dismiss_count + ?), updated_at = ?"
                " WHERE key_hash = ?",
                (delta, now, key_hash),
            )
            conn.execute(
                "DELETE FROM triage_rules WHERE key_hash = ? AND dismiss_count = 0", (key_hash,)
            )

    def total(self) -> int:
        return int(self._db.query("SELECT count(*) FROM triage_rules")[0][0])

    def clear(self) -> int:
        with self._db.transaction() as conn:
            return int(conn.execute("DELETE FROM triage_rules").rowcount)
