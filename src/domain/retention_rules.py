"""보관 D-day·삭제 전 알림 규칙(RET-01·02).

종결 시각 + 보관 기간 = 삭제 예정 시각. 삭제 2일 전부터 G-Knowledge 미등록 완료 업무를 알린다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from domain.models import Task, TaskStatus

ALERT_BEFORE: Final = timedelta(days=2)


@dataclass(frozen=True, slots=True)
class RetentionInfo:
    delete_at: datetime
    days_left: int  # 올림 일수(D-day 표시)
    alert: bool  # 삭제 2일 전 이내 + G-Knowledge 미등록 완료 업무


def retention_info(task: Task, now: datetime, retention: timedelta) -> RetentionInfo | None:
    if task.closed_at is None or task.status not in (TaskStatus.DONE, TaskStatus.DISMISSED):
        return None
    delete_at = task.closed_at + retention
    remaining = delete_at - now
    days_left = max(0, math.ceil(remaining.total_seconds() / 86400))
    alert = (
        task.status is TaskStatus.DONE
        and not task.gk_registered
        and timedelta(0) < remaining <= ALERT_BEFORE
    )
    return RetentionInfo(delete_at, days_left, alert)


def tasks_needing_alert(tasks: list[Task], now: datetime, retention: timedelta) -> list[Task]:
    result = []
    for task in tasks:
        info = retention_info(task, now, retention)
        if info is not None and info.alert:
            result.append(task)
    return result
