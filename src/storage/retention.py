"""보관기간 삭제 작업(RET-01, RET-04).

종결(완료·무시) 후 보관 기간이 지난 업무와 그 메일 참조·이벤트·히스토리 카드를 지운다.
진행 중 업무는 대상이 아니다. G-Knowledge 등록 여부와 관계없이 삭제한다(RET-04).
삭제 후 VACUUM으로 빈 페이지까지 정리하고(secure_delete와 함께 복구 불가), 로그에는 건수만 남긴다.

지표 원장은 `metrics_ledger_keep`이 false면 같은 규칙으로 지우고, true면 유지한다(기획서 결정 대기).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from domain.models import CLOSED_STATUSES, TaskStatus
from domain.ports import Clock
from security.logging_policy import EventType, Site, log_event
from storage.db import Database, to_db_time

logger = logging.getLogger(__name__)

# (테이블, 업무 ID 컬럼). 자식 테이블을 먼저 지운다.
_CHILD_TABLES = (
    ("task_event", "task_id"),
    ("history_card", "task_id"),
    ("mail_ref", "task_id"),
    ("task", "id"),
)


@dataclass(frozen=True, slots=True)
class PurgeResult:
    tasks: int = 0
    mail_refs: int = 0
    events: int = 0
    history_cards: int = 0
    ledger_rows: int = 0
    ai_calls: int = 0

    @property
    def total(self) -> int:
        return (
            self.tasks
            + self.mail_refs
            + self.events
            + self.history_cards
            + self.ledger_rows
            + self.ai_calls
        )


class RetentionJob:
    def __init__(
        self, db: Database, clock: Clock, *, retention_days: int, keep_ledger: bool
    ) -> None:
        if retention_days < 1:
            raise ValueError("보관 기간은 1일 이상이어야 합니다")
        self._db = db
        self._clock = clock
        self._retention = timedelta(days=retention_days)
        self._keep_ledger = keep_ledger

    @property
    def retention(self) -> timedelta:
        return self._retention

    def run(self) -> PurgeResult:
        cutoff = to_db_time(self._clock.now() - self._retention)
        closed = tuple(s.value for s in CLOSED_STATUSES)
        marks = ",".join("?" for _ in closed)
        # 종결 후 보관 기간 경과 + 처리하지 않은 제안(생성 후 보관 기간 경과)
        expired = (
            f"SELECT id FROM task WHERE (status IN ({marks}) AND closed_at <= ?)"  # noqa: S608
            " OR (status = ? AND created_at <= ?)"
        )
        params = (*closed, cutoff, TaskStatus.SUGGESTED.value, cutoff)
        try:
            with self._db.transaction() as conn:
                # 연결 행은 외래 키 CASCADE로도 지워지지만, 건수를 세려고 먼저 지운다.
                # 쿼리 조각은 모두 이 모듈의 상수로만 만들고 값은 매개변수로 넘긴다.
                counts = {
                    table: conn.execute(
                        f"DELETE FROM {table} WHERE {key} IN ({expired})",  # noqa: S608
                        params,
                    ).rowcount
                    for table, key in _CHILD_TABLES
                }
                events = counts["task_event"]
                cards = counts["history_card"]
                mails = counts["mail_ref"]
                tasks = counts["task"]
                # 업무로 이어지지 않은 메일 참조·이벤트도 수신·발생 후 보관 기간이 지나면 지운다.
                mails += conn.execute(
                    "DELETE FROM mail_ref WHERE task_id IS NULL AND received_at <= ?", (cutoff,)
                ).rowcount
                events += conn.execute(
                    "DELETE FROM task_event WHERE task_id IS NULL AND occurred_at <= ?",
                    (cutoff,),
                ).rowcount
                ai_calls = conn.execute(
                    "DELETE FROM ai_call_log WHERE called_at <= ?", (cutoff,)
                ).rowcount
                ledger = 0
                if not self._keep_ledger:
                    ledger = conn.execute(
                        "DELETE FROM metric_ledger WHERE completed_at <= ?", (cutoff,)
                    ).rowcount
        except Exception:
            log_event(EventType.RETENTION_PURGE, Site.APP, success=False)
            raise
        result = PurgeResult(tasks, mails, events, cards, ledger, ai_calls)
        if result.total:
            self._db.vacuum()
        logger.info(
            "보관기간 삭제: 업무 %d, 메일참조 %d, 이벤트 %d, 히스토리 %d, 지표원장 %d, AI이력 %d",
            tasks,
            mails,
            events,
            cards,
            ledger,
            ai_calls,
        )
        log_event(EventType.RETENTION_PURGE, Site.APP, success=True)
        return result
