"""주간보고 초안의 업무별 간략 요약 모으기(결정기록 0042).

도메인 순수 로직(`domain.weekly.brief`)에 업무별 메일 참조(원문 없음, 마스킹된 요약만)를 넘긴다.
AI를 새로 부르지 않는다(메일 수집 때 만든 요약을 쓴다).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from app.container import Container
from domain.models import Task
from domain.weekly.brief import TaskBrief, task_brief


def week_briefs(
    container: Container, sections: Mapping[str, list[Task]], now: datetime
) -> dict[int, TaskBrief]:
    briefs: dict[int, TaskBrief] = {}
    for tasks in sections.values():
        for task in tasks:
            if task.id is not None and task.id not in briefs:
                refs = container.mail.refs_for_task(task.id)
                briefs[task.id] = task_brief(task, refs, now)
    return briefs
