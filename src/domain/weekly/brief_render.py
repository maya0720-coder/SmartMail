"""기본 주간보고 양식(개인 주간 보고서) 초안 HTML(WIKI-02, 결정기록 0042). 순수 로직.

양식 구조는 skills.sh `claude-office-skills/weekly-report`의 'Individual Weekly Report'를 따랐다
(요약 → 금주 실적 → 진행 중 업무 표 → 이슈·요청 → 차주 계획). 사내 머리글(금주 실적·차주 계획·
이슈·요청, 참고 링크 양식이 있으면 그 머리글)은 그대로 쓴다. 모든 글자는 마스킹된 값이며
여기서 이스케이프한다. 조직 양식 프로필이 있으면 이 양식 대신 프로필 순서를 쓴다(0037).
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import datetime
from typing import Final

from domain.models import Task
from domain.weekly.brief import EMPTY_BRIEF, TaskBrief
from domain.weekly.report import MAX_ITEM_CHARS, WikiTemplate, item_text

HEADING_SUMMARY: Final = "요약"
HEADING_STATUS: Final = "업무별 현황"
STATUS_COLUMNS: Final = ("업무", "진행 상황", "수행 내용", "완료 예정")
MAX_PRIORITIES: Final = 5  # 차주 계획(우선순위) 최대 항목
_FAR: Final = datetime.max.replace(tzinfo=None)


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _brief(briefs: Mapping[int, TaskBrief], task: Task) -> TaskBrief:
    return briefs.get(task.id or -1, EMPTY_BRIEF)


def brief_item(task: Task, template: WikiTemplate, brief: TaskBrief) -> str:
    """간단 편집·조직 양식 목록용 한 줄: '[구분] 제목 — 수행 내용'(수행 내용이 없으면 제목만)."""
    head = item_text(task, template)
    line = f"{head} — {brief.work}" if brief.work else head
    return line[:MAX_ITEM_CHARS]


def _title(task: Task, template: WikiTemplate) -> str:
    return f"<b>{_esc(item_text(task, template))}</b>"


def _summary(sections: Mapping[str, list[Task]], briefs: Mapping[int, TaskBrief]) -> str:
    done, planned, issues = (len(sections[k]) for k in ("done", "planned", "issues"))
    waiting = sum(1 for t in sections["planned"] if "회신 대기" in _brief(briefs, t).progress)
    parts = [f"완료 {done}건", f"진행 {planned}건", f"이슈 {issues}건"]
    if waiting:
        parts.append(f"회신 대기 {waiting}건")
    return f"<p>{_esc(' · '.join(parts))}</p>"


def _done(tasks: list[Task], template: WikiTemplate, briefs: Mapping[int, TaskBrief]) -> str:
    if not tasks:
        return "<p>-</p>"
    items = []
    for task in tasks:
        brief = _brief(briefs, task)
        tail = " — ".join(x for x in (brief.work, brief.eta) if x and x != "-")
        items.append(f"<li>{_title(task, template)}{' — ' + _esc(tail) if tail else ''}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _status_table(
    tasks: list[Task], template: WikiTemplate, briefs: Mapping[int, TaskBrief]
) -> str:
    if not tasks:
        return "<p>-</p>"
    head = "<tr>" + "".join(f"<th>{c}</th>" for c in STATUS_COLUMNS) + "</tr>"
    rows = []
    for task in tasks:
        brief = _brief(briefs, task)
        cells = (item_text(task, template), brief.progress, brief.work or "-", brief.eta)
        rows.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr>")
    return f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def _issues(tasks: list[Task], template: WikiTemplate, briefs: Mapping[int, TaskBrief]) -> str:
    if not tasks:
        return "<p>-</p>"
    items = []
    for task in tasks:
        brief = _brief(briefs, task)
        tail = " · ".join(x for x in (brief.progress, brief.work) if x)
        items.append(f"<li>{_title(task, template)}{' — ' + _esc(tail) if tail else ''}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _priorities(tasks: list[Task], template: WikiTemplate, briefs: Mapping[int, TaskBrief]) -> str:
    """차주 계획: 기한이 이른 순(기한 없으면 뒤) 상위 몇 건."""
    if not tasks:
        return "<p>-</p>"
    ordered = sorted(
        tasks, key=lambda t: t.due_at.replace(tzinfo=None) if t.due_at is not None else _FAR
    )[:MAX_PRIORITIES]
    items = []
    for task in ordered:
        brief = _brief(briefs, task)
        suffix = f" (기한 {brief.eta})" if brief.eta and brief.eta != "-" else ""
        work = f" — {_esc(brief.work)}" if brief.work else ""
        items.append(f"<li>{_title(task, template)}{_esc(suffix)}{work}</li>")
    return "<ol>" + "".join(items) + "</ol>"


def render_brief_body(
    sections: Mapping[str, list[Task]],
    briefs: Mapping[int, TaskBrief],
    template: WikiTemplate,
) -> str:
    """본인 머리글을 뺀 본문 HTML(편집기 초기값). 머리글은 템플릿(참고 링크 양식 반영) 값."""
    return "".join(
        (
            f"<h3>{HEADING_SUMMARY}</h3>",
            _summary(sections, briefs),
            f"<h3>{_esc(template.heading_done)}</h3>",
            _done(sections["done"], template, briefs),
            f"<h3>{HEADING_STATUS}</h3>",
            _status_table(sections["planned"], template, briefs),
            f"<h3>{_esc(template.heading_issues)}</h3>",
            _issues(sections["issues"], template, briefs),
            f"<h3>{_esc(template.heading_planned)}</h3>",
            _priorities(sections["planned"], template, briefs),
        )
    )
