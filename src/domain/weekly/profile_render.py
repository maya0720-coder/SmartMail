"""조직 양식 프로필로 주간보고 초안 HTML 만들기(N4, WIKI-02, 결정기록 0037).

블록 순서·열 순서 그대로 만든다. 모든 글자는 마스킹·이스케이프한다(순수 로직).
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Final

from domain.clock import KST
from domain.models import STATUS_LABELS, Task
from domain.weekly.template_profile import DATA_SOURCES, Bindings, ProfileBlock
from security.masking import mask

MAX_CELL_CHARS: Final = 300


def _date(value: datetime | None) -> str:
    return "-" if value is None else value.astimezone(KST).strftime("%m/%d")


def row_value(task: Task, name: str) -> str:
    values = {
        "title": task.title,
        "module": task.module_tag or "기타",
        "dept": task.requester_dept or "-",
        "status": STATUS_LABELS[task.status],
        "due": _date(task.due_at),
        "closed": _date(task.closed_at),
        "progress": task.progress_digest or "",
        "note": "",
    }
    text = " ".join(values[name].split())
    return mask(text).value[:MAX_CELL_CHARS]


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _empty_rows(count: int, width: int) -> str:
    cells = "<td><br /></td>" * width
    return f"<tr>{cells}</tr>" * max(1, min(count, 3))


def _table(block: ProfileBlock, fields: tuple[str, ...], tasks: list[Task] | None) -> str:
    head = "<tr>" + "".join(f"<th>{_esc(c)}</th>" for c in block.columns) + "</tr>"
    if tasks:
        body = "".join(
            "<tr>"
            + "".join(f"<td>{_esc(row_value(t, f)) or '<br />'}</td>" for f in fields)
            + "</tr>"
            for t in tasks
        )
    else:
        body = _empty_rows(block.sample_rows, len(block.columns))
    return f"<table><thead>{head}</thead><tbody>{body}</tbody></table>"


def _list(style: str, items: list[str]) -> str:
    tag = "ol" if style == "ol" else "ul"
    inner = "".join(f"<li>{_esc(i)}</li>" for i in items) or "<li><br /></li>"
    return f"<{tag}>{inner}</{tag}>"


def render_profile(
    blocks: list[ProfileBlock],
    bindings: Bindings,
    sections: Mapping[str, list[Task]],
    item_text: Callable[[Task], str],
) -> str:
    """양식 블록 순서대로 초안 HTML을 만든다(내 영역 본문, 본인 머리글 제외).

    머리글에 연결된 데이터는 그 머리글 아래 첫 표(행) 또는 첫 목록(항목)에 넣는다.
    아래에 표·목록이 없으면 머리글 바로 뒤에 목록을 만든다.
    """
    parts: list[str] = []
    source: str | None = None
    filled = True

    def close_section() -> None:
        if source in DATA_SOURCES and not filled:
            parts.append(_list("ul", [item_text(t) for t in sections.get(source, [])]))

    for index, block in enumerate(blocks):
        if block.kind == "heading":
            close_section()
            source = bindings.headings.get(index, "none")
            filled = False
            parts.append(f"<h{block.level}>{_esc(block.text)}</h{block.level}>")
        elif block.kind == "paragraph":
            parts.append(f"<p>{_esc(block.text)}</p>")
        else:
            data = None
            if source in DATA_SOURCES and not filled:
                data = list(sections.get(source, []))
                filled = True
            if block.kind == "table":
                fields = bindings.columns.get(index, ("note",) * len(block.columns))
                parts.append(_table(block, fields, data))
            else:
                parts.append(_list(block.style, [item_text(t) for t in data or []]))
    close_section()
    return "".join(parts)
