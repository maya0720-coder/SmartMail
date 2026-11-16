"""대시보드 기간 필터(A1, MET·TODO-03, 결정기록 0039).

현재 기간 [시작, 지금]에 다음 중 하나라도 맞으면 그 기간의 업무로 본다(D2 기본안).
- 요청일(없으면 만든 시각) 또는 최근 메일 시각이 기간 안
- 완료일이 기간 안
- 이월: 기간 시작 전에 생겼지만 아직 끝나지 않은 업무(완료·무시가 아님). carry가 참일 때만

순수 로직이다. 기간 시작은 `domain/metrics/periods.bucket_of`로 정한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from domain.models import Task, TaskStatus

CLOSED_STATUSES: Final = frozenset({TaskStatus.DONE, TaskStatus.DISMISSED})


def _inside(moment: datetime | None, start: datetime, end: datetime) -> bool:
    return moment is not None and start <= moment <= end


def in_period(
    task: Task, last_mail_at: datetime | None, start: datetime, end: datetime, *, carry: bool
) -> bool:
    began = task.requested_at or task.created_at
    if _inside(began, start, end) or _inside(last_mail_at, start, end):
        return True
    if _inside(task.closed_at, start, end):
        return True
    return carry and task.status not in CLOSED_STATUSES and began < start
