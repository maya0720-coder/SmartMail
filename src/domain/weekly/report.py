"""주간보고 초안 분류와 렌더링(WIKI-01·02).

- 한 주(목 00:00 ~ 수 23:59, KST) 업무를 금주 실적 / 차주 계획 / 이슈·요청으로 나눈다.
- 렌더링은 항상 HTML 이스케이프를 거친다(미리보기·Confluence 저장 형식 공통).
- 본인 영역 교체는 `domain.weekly.sections`가 맡는다(WIKI-04).
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from domain.calendar import week_range
from domain.clock import KST
from domain.errors import UserInputError
from domain.models import STATUS_LABELS, Task, TaskStatus
from domain.weekly.form import FormLayout
from domain.weekly.rich import MAX_HTML_BYTES
from domain.weekly.sections import OwnSectionError as OwnSectionError
from domain.weekly.sections import replace_own_section as replace_own_section
from domain.weekly.title_rule import render_title
from security.masking import mask

MAX_ITEMS = 50
_OPEN = (TaskStatus.WAITING, TaskStatus.IN_PROGRESS, TaskStatus.DONE_CANDIDATE)
MAX_ITEM_CHARS = 300
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def single_line(text: str) -> str:
    """줄바꿈·제어 문자를 공백 하나로 바꾼다(줄 맨 앞 마크업 삽입 방지)."""
    return _CONTROL.sub(" ", text).strip()


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
    layout: FormLayout | None = None  # 참고 링크 양식의 머리글(없으면 기본 양식)
    # 미리보기 편집기 HTML(결정기록 0030). 있으면 목록 대신 이 본문을 쓴다(정리·마스킹 후)
    html: str | None = None

    def validate(self) -> None:
        for items in (self.done, self.planned, self.issues):
            if len(items) > MAX_ITEMS or any(len(i) > MAX_ITEM_CHARS for i in items):
                raise UserInputError("주간보고 항목이 너무 많거나 깁니다")
        if self.html is not None and len(self.html.encode("utf-8")) > MAX_HTML_BYTES:
            raise UserInputError("편집 내용이 너무 큽니다(200KB 이하)")

    def digest(self) -> str:
        """미리보기·승인 대상이 같은 내용인지 확인하는 지문."""
        parts = [
            self.week_start.isoformat(),
            *self.done,
            "\x1d",
            *self.planned,
            "\x1d",
            *self.issues,
        ]
        if self.layout is not None:
            parts += ["\x1d", *self.layout.headings()]
        if self.html is not None:
            parts += ["\x1d", "html", self.html]
        payload = "\x1e".join(parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def with_layout(template: WikiTemplate, layout: FormLayout | None) -> WikiTemplate:
    """참고 링크 양식의 머리글을 반영한 템플릿(없으면 그대로)."""
    if layout is None:
        return template
    done, planned, issues = layout.headings()
    return replace(template, heading_done=done, heading_planned=planned, heading_issues=issues)


def _item(task: Task, template: WikiTemplate) -> str:
    text = (
        template.item_format.replace("{module}", task.module_tag or "기타")
        .replace("{title}", task.title)
        .replace("{dept}", task.requester_dept or "-")
        .replace("{status}", STATUS_LABELS[task.status])
    )
    # 마스킹한 뒤에 자른다(경계에 걸린 민감정보 일부 노출 방지).
    return mask(text).value[:MAX_ITEM_CHARS]


def classify_week_tasks(tasks: list[Task], moment: datetime) -> dict[str, list[Task]]:
    """WIKI-01 자동 분류(업무 단위). 양식 프로필의 표 행을 만들 때도 쓴다(결정기록 0037)."""
    start, end = week_range(moment)
    sections: dict[str, list[Task]] = {"done": [], "planned": [], "issues": []}
    for task in tasks:
        if task.weekly_section is not None and task.status not in (
            TaskStatus.SUGGESTED,
            TaskStatus.DISMISSED,
        ):
            # 사용자가 고른 위치가 자동 분류보다 우선한다(결정기록 0015). 지난주 이전 완료는 제외.
            old_done = task.status is TaskStatus.DONE and (
                task.closed_at is None or not start <= task.closed_at < end
            )
            if task.weekly_section in sections and not old_done:
                sections[task.weekly_section].append(task)
            continue  # 'none'(넣지 않음) 포함
        if task.status is TaskStatus.DONE:
            if task.closed_at is not None and start <= task.closed_at < end:
                sections["done"].append(task)
        elif task.status is TaskStatus.ON_HOLD or (
            task.status in _OPEN and task.due_at is not None and task.due_at < moment
        ):
            sections["issues"].append(task)  # 보류·기한 지연
        elif task.status in _OPEN:
            sections["planned"].append(task)
    for items in sections.values():
        del items[MAX_ITEMS:]
    return sections


def item_text(task: Task, template: WikiTemplate) -> str:
    """목록 항목 한 줄(항목 형식, 마스킹 후 자름)."""
    return _item(task, template)


def classify_week(tasks: list[Task], moment: datetime, template: WikiTemplate) -> WeeklyDraft:
    """WIKI-01 자동 분류."""
    sections = classify_week_tasks(tasks, moment)
    draft = WeeklyDraft(week_range(moment)[0])
    draft.done.extend(_item(t, template) for t in sections["done"])
    draft.planned.extend(_item(t, template) for t in sections["planned"])
    draft.issues.extend(_item(t, template) for t in sections["issues"])
    return draft


def render_section(draft: WeeklyDraft, template: WikiTemplate, name: str) -> str:
    """본인 영역 HTML(Confluence 저장 형식과 호환되는 단순 XHTML). 모든 값은 이스케이프한다.

    편집기 본문(draft.html, 이미 정리·마스킹된 값)이 있으면 본인 머리글 뒤에 그대로 붙인다.
    """
    head = f"<h2>{html.escape(template.heading(name))}</h2>"
    if draft.html is not None:
        return head + draft.html
    return head + render_body(draft, template)


def render_body(draft: WeeklyDraft, template: WikiTemplate) -> str:
    """본인 머리글을 뺀 본문(편집기 초기값 draft_html). 모든 값은 이스케이프한다."""
    parts: list[str] = []
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
