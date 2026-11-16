"""G-Knowledge 백그라운드 등록 상태 저장(결정기록 0035).

업무(task) 행에 상태·시각·사유 코드만 둔다. 초안(제목·본문)은 저장하지 않는다(큐는 메모리).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from storage.db import GK_STATES, Database, from_db_time, to_db_time

MAX_REASON: Final = 32


@dataclass(frozen=True, slots=True)
class GkStateRow:
    task_id: int
    state: str
    at: datetime | None
    reason: str | None


def _check(state: str, reason: str | None) -> None:
    if state not in GK_STATES:
        raise ValueError("알 수 없는 등록 상태입니다")
    if reason is not None and (
        len(reason) > MAX_REASON or not reason.replace("_", "").isalpha() or not reason.isascii()
    ):
        raise ValueError("사유는 영문 소문자 코드만 받습니다")


class SqliteGkStates:
    def __init__(self, db: Database) -> None:
        self._db = db

    def set(self, task_id: int, state: str, at: datetime, reason: str | None = None) -> None:
        _check(state, reason)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE task SET gk_state = ?, gk_state_at = ?, gk_state_reason = ? WHERE id = ?",
                (state, to_db_time(at), reason, task_id),
            )

    def clear(self, task_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE task SET gk_state = NULL, gk_state_at = NULL, gk_state_reason = NULL"
                " WHERE id = ?",
                (task_id,),
            )

    def all(self) -> list[GkStateRow]:
        rows = self._db.query(
            "SELECT id, gk_state, gk_state_at, gk_state_reason FROM task WHERE gk_state IS NOT NULL"
        )
        return [
            GkStateRow(
                int(r["id"]),
                str(r["gk_state"]),
                from_db_time(r["gk_state_at"]),
                None if r["gk_state_reason"] is None else str(r["gk_state_reason"]),
            )
            for r in rows
        ]
