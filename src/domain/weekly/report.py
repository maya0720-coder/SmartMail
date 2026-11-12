"""주간보고 초안 분류와 렌더링(WIKI-01·02).

- 한 주(목 00:00 ~ 수 23:59, KST) 업무를 금주 실적 / 차주 계획 / 이슈·요청으로 나눈다.
- 렌더링은 항상 HTML 이스케이프를 거친다(미리보기·Confluence 저장 형식 공통).
- 본인 영역 교체는 `domain.weekly.sections`가 맡는다(WIKI-04).
"""

from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from domain.calendar import week_range
from domain.clock import KST
from domain.errors import UserInputError
from domain.models import STATUS_LABELS, Task, TaskStatus
from domain.weekly.sections import OwnSectionError as OwnSectionError
from domain.weekly.sections import replace_own_section as replace_own_section
from domain.weekly.title_rule import render_title
from security.masking import mask

MAX_ITEMS = 50
_OPEN = (TaskStatus.WAITING, TaskStatus.IN_PROGRESS, TaskStatus.DONE_CANDIDATE)
MAX_ITEM_CHARS = 300


@dataclass(frozen=True, slots=True)
class WikiTemplate:
    page_title_format: str = "ERP_{yymmdd}"
    title_date_offset_days: int = 0  # 주 시작(목)과 같은 날. 사용자 결정 2026-11-12
    space_key: str = ""
    own_section_heading: str = "{name}"
    heading_done: str = "금주 실적"
    heading_planned: str = "차주 계획"
    heading_issues: str = "이슈·요청"
    item_format: str = "[{module}] {title} (요청: {dept})"

    def page_title(self, week_start: datetime) -> str:
        day = (week_start + timedelta(days=self.title_date_offset_days)).astimezone(KST)
        return render_title(self.page_title_format, day)

    def heading(self, name: str) -> str:
        return self.own_section_heading.replace("{name}", name)


@dataclass
class WeeklyDraft:
    week_start: datetime
    done: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def validate(self) -> None:
        for items in (self.done, self.planned, self.issues):
            if len(items) > MAX_ITEMS or any(len(i) > MAX_ITEM_CHARS for i in items):
                raise UserInputError("주간보고 항목이 너무 많거나 깁니다")

    def digest(self) -> str:
        """미리보기·승인 대상이 같은 내용인지 확인하는 지문."""
        payload = "\x1e".join(
            [self.week_start.isoformat(), *self.done, "\x1d", *self.planned, "\x1d", *self.issues]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _item(task: Task, template: WikiTemplate) -> str:
    text = (
        template.item_format.replace("{module}", task.module_tag or "기타")
        .replace("{title}", task.title)
        .replace("{dept}", task.requester_dept or "-")
        .replace("{status}", STATUS_LABELS[task.status])
    )
    # 마스킹한 뒤에 자른다(경계에 걸린 민감정보 일부 노출 방지).
    return mask(text).value[:MAX_ITEM_CHARS]


def classify_week(tasks: list[Task], moment: datetime, template: WikiTemplate) -> WeeklyDraft:
    """WIKI-01 자동 분류."""
    start, end = week_range(moment)
    draft = WeeklyDraft(start)
    for task in tasks:
        if task.status is TaskStatus.DONE:
            if task.closed_at is not None and start <= task.closed_at < end:
                draft.done.append(_item(task, template))
        elif task.status is TaskStatus.ON_HOLD or (
            task.status in _OPEN and task.due_at is not None and task.due_at < moment
        ):
            draft.issues.append(_item(task, template))  # 보류·기한 지연
        elif task.status in _OPEN:
            draft.planned.append(_item(task, template))
    for items in (draft.done, draft.planned, draft.issues):
        del items[MAX_ITEMS:]
    return draft


def render_section(draft: WeeklyDraft, template: WikiTemplate, name: str) -> str:
    """본인 영역 HTML(Confluence 저장 형식과 호환되는 단순 XHTML). 모든 값은 이스케이프한다."""
    parts = [f"<h2>{html.escape(template.heading(name))}</h2>"]
    for heading, items in (
        (template.heading_done, draft.done),
        (template.heading_planned, draft.planned),
        (template.heading_issues, draft.issues),
    ):
        parts.append(f"<h3>{html.escape(heading)}</h3>")
        if items:
            parts.append("<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ul>")
        else:
            parts.append("<p>-</p>")
    return "".join(parts)
