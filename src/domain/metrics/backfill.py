"""지표 원장 누락 보정(A1, MET-01~04, 결정기록 0039).

완료 상태인데 원장에 없는 업무가 있으면 앱 시작 때 1회 원장을 채운다.
- 숫자 항목만 쓴다(완료 시각·기한 유무·기한 준수·지식 등록). 제목·본문·상대방 정보는 넣지 않는다.
- 완료 시각은 업무의 완료일(closed_at)이다. 완료일이 없으면 채우지 않는다(추정하지 않음).
- 업무가 보관기간으로 지워지면 원장도 `SMARTMAIL_METRICS_LEDGER_KEEP` 설정대로 함께 처리된다
  (보정은 남아 있는 업무만 대상으로 하므로 설정을 바꾸지 않는다).
"""

from __future__ import annotations

from collections.abc import Iterable

from domain.models import LedgerEntry, Task, TaskStatus
from domain.ports import LedgerRepository


def backfill_ledger(tasks: Iterable[Task], ledger: LedgerRepository, assignee: str) -> int:
    """원장에 없는 완료 업무를 채우고 채운 건수를 돌려준다."""
    known = {entry.task_id for entry in ledger.list_entries()}
    added = 0
    for task in tasks:
        if task.status is not TaskStatus.DONE or task.id is None or task.closed_at is None:
            continue
        if task.id in known:
            continue
        ledger.upsert(
            LedgerEntry(
                task_id=task.id,
                completed_at=task.closed_at,
                assignee=assignee,
                has_due=task.due_at is not None,
                due_met=None if task.due_at is None else task.closed_at <= task.due_at,
                gk_registered=task.gk_registered,
            )
        )
        added += 1
    return added
