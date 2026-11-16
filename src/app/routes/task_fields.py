"""업무 API 응답의 보조 필드(결정기록 0027~0029).

- 요약 상태(summary_state): 첫 메일 기준 pending|running|done|kept|missing. running은 메모리 합성.
- 스레드 최종 메일(last_mail_at·last_mail_direction·mail_count): 행 정렬·묶음 기준.
- 저장값이 없는(이관 전) 업무는 메일 참조로 계산하고, 그것도 없으면 요청일 기준으로 둔다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from domain.models import MailRef, Task, summary_state_of
from domain.tasks.requester import CompanyResolver

RUNNING = "running"


def first_ref(refs: Sequence[MailRef]) -> MailRef | None:
    return min(refs, key=lambda r: r.received_at) if refs else None


def last_ref(refs: Sequence[MailRef]) -> MailRef | None:
    return max(refs, key=lambda r: r.received_at) if refs else None


def summary_state(task: Task, refs: Sequence[MailRef], running: set[int]) -> str | None:
    """업무 제목 요약 상태. 수동 업무(메일 없음)는 None."""
    first = first_ref(refs)
    if first is None:
        return None
    state = summary_state_of(first)
    if task.id in running and state == "pending":
        return RUNNING
    return state


def last_mail(task: Task, refs: Sequence[MailRef]) -> tuple[datetime | None, str | None]:
    """(마지막 메일 시각, 방향). 저장값 → 메일 참조 → 요청일 순으로 고른다."""
    last = last_ref(refs)
    at = task.last_mail_at or (last.received_at if last else None)
    direction = task.last_mail_direction or (last.direction if last else None)
    if at is None:
        at = task.requested_at
    return at, direction


def thread_fields(
    task: Task, refs: Sequence[MailRef], running: set[int], iso: Any
) -> dict[str, Any]:
    at, direction = last_mail(task, refs)
    return {
        "summary_state": summary_state(task, refs, running),
        "last_mail_at": iso(at),
        "last_mail_direction": direction,
        "mail_count": len(refs),
        # 내가 마지막으로 회신한 스레드(완료 후보 판단 보조)
        "replied_last": direction == "out",
    }


def requester_fields(resolver: CompanyResolver, task: Task) -> dict[str, Any]:
    """요청자 표시(결정기록 0027). 외부면 회사명(매핑·AI 추정·사용자 확정) 또는 외부(도메인)."""
    label, source = resolver.label(task.requester_kind, task.requester_domain)
    return {
        "requester_kind": task.requester_kind,
        "requester_domain": task.requester_domain,
        "requester_label": label if label is not None else task.requester_dept,
        "requester_source": source,
    }
