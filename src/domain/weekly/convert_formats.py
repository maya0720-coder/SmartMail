"""정리·마스킹된 주간보고 HTML → 플랫폼별 복사 형식(C2, WIKI-05, 결정기록 0041).

입력은 `CleanHtml`(정리·마스킹을 거친 HTML)만 받는다. 순수 로직이며 외부로 보내지 않는다.
- to_storage_xhtml: Confluence 저장 형식(위키 '소스 편집'에 붙여넣기)
- to_teams_html: 표·굵게·목록을 유지한 HTML
- to_mail_html: 표 테두리·글꼴을 인라인 스타일로 넣은 HTML(Outlook 붙여넣기용)
- to_slack_html: 굵게·목록만 남긴 HTML(Slack 서식 붙여넣기용). 전체 멘션은 무력화한다
- to_markdown: Markdown(표는 GFM 표, 칸 안의 `|`는 이스케이프)
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final

from domain.weekly.convert import to_platform_text
from domain.weekly.links import Platform
from domain.weekly.report import WeeklyDraft, WikiTemplate, render_body, render_section, with_layout
from domain.weekly.rich import VOID_TAGS, CleanHtml, clean_html
from domain.weekly.targets import ExportFormat

_MENTION: Final = re.compile(r"@(channel|here|everyone)", re.IGNORECASE)
_MD_SPECIAL: Final = re.compile(r"([\\`*_\[\]<>])")
MAIL_FONT: Final = "font-family: 'Malgun Gothic', sans-serif; font-size: 10pt"
MAIL_TABLE: Final = "border-collapse: collapse; border: 1px solid #999999"
MAIL_CELL: Final = "border: 1px solid #999999; padding: 4px 8px"
MAIL_HEAD: Final = MAIL_CELL + "; background-color: #f2f2f2"


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs})
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def tree(clean: CleanHtml) -> Node:
    builder = _TreeBuilder()
    builder.feed(clean.value)
    builder.close()
    return builder.root


def text_of(node: Node | str) -> str:
    if isinstance(node, str):
        return node
    if node.tag == "br":
        return " "
    return "".join(text_of(child) for child in node.children)


def _flat(text: str) -> str:
    return " ".join(text.split())


# ---- HTML 다시 쓰기(속성은 정리기를 통과한 값만 있다) ----
def _emit(node: Node | str, style_for: dict[str, str] | None = None) -> str:
    if isinstance(node, str):
        return html.escape(node, quote=False)
    attrs = dict(node.attrs)
    extra = (style_for or {}).get(node.tag)
    if extra:
        attrs["style"] = extra + ("; " + attrs["style"] if attrs.get("style") else "")
    attr_text = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in attrs.items())
    if node.tag in VOID_TAGS:
        return f"<{node.tag}{attr_text} />"
    inner = "".join(_emit(child, style_for) for child in node.children)
    return f"<{node.tag}{attr_text}>{inner}</{node.tag}>"


def _owner(owner_heading: str | None) -> str:
    return "" if not owner_heading else f"<h2>{html.escape(owner_heading, quote=False)}</h2>"


def to_storage_xhtml(clean: CleanHtml, owner_heading: str | None = None) -> str:
    return _owner(owner_heading) + clean.value


def to_teams_html(clean: CleanHtml, owner_heading: str | None = None) -> str:
    return _owner(owner_heading) + clean.value


def to_mail_html(clean: CleanHtml, owner_heading: str | None = None) -> str:
    styles = {"table": MAIL_TABLE, "th": MAIL_HEAD, "td": MAIL_CELL}
    body = "".join(_emit(child, styles) for child in tree(clean).children)
    return f'<div style="{MAIL_FONT}">{_owner(owner_heading)}{body}</div>'


def _slack_node(node: Node | str) -> str:
    if isinstance(node, str):
        return html.escape(_MENTION.sub("@​\\1", node), quote=False)
    inner = "".join(_slack_node(child) for child in node.children)
    if node.tag in ("b", "strong", "ul", "ol", "li"):
        return f"<{node.tag}>{inner}</{node.tag}>"
    if node.tag in ("h3", "h4"):
        return f"<p><b>{inner}</b></p>"
    if node.tag == "tr":
        cells = [_slack_node(c) for c in node.children if isinstance(c, Node)]
        return "<p>" + " | ".join(cells) + "</p>"
    if node.tag in ("p", "div", "blockquote", "pre", "caption"):
        return f"<p>{inner}</p>"
    if node.tag == "br":
        return "<br />"
    return inner


def to_slack_html(clean: CleanHtml, owner_heading: str | None = None) -> str:
    head = "" if not owner_heading else f"<p><b>{html.escape(owner_heading, quote=False)}</b></p>"
    return head + "".join(_slack_node(child) for child in tree(clean).children)


# ---- Markdown ----
def _md_escape(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def _md_inline(node: Node | str) -> str:
    if isinstance(node, str):
        return _md_escape(node.replace("\n", " "))
    inner = "".join(_md_inline(child) for child in node.children)
    marks = {"b": "**", "strong": "**", "em": "*", "i": "*", "s": "~~", "code": "`"}
    if node.tag in marks and inner.strip():
        return f"{marks[node.tag]}{inner.strip()}{marks[node.tag]}"
    if node.tag == "a" and node.attrs.get("href"):
        return f"[{inner}]({node.attrs['href']})"
    if node.tag == "br":
        return " "
    return inner


def _md_cell(node: Node) -> str:
    return _flat(_md_inline(node)).replace("|", "\\|")


def _md_table(table: Node) -> list[str]:
    rows: list[list[str]] = []
    for part in table.children:
        if not isinstance(part, Node):
            continue
        for row in (
            [part] if part.tag == "tr" else [c for c in part.children if isinstance(c, Node)]
        ):
            if row.tag == "tr":
                rows.append([_md_cell(c) for c in row.children if isinstance(c, Node)])
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return lines


def _md_list(node: Node, depth: int) -> list[str]:
    lines: list[str] = []
    number = int(node.attrs.get("start", "1") or 1)
    for child in node.children:
        if not isinstance(child, Node) or child.tag != "li":
            continue
        mark = f"{number}." if node.tag == "ol" else "-"
        number += 1
        text = _flat("".join(_md_inline(c) for c in child.children if not _is_list(c)))
        lines.append("  " * depth + f"{mark} {text}")
        for sub in child.children:
            if isinstance(sub, Node) and _is_list(sub):
                lines += _md_list(sub, depth + 1)
    return lines


def _is_list(node: Node | str) -> bool:
    return isinstance(node, Node) and node.tag in ("ul", "ol")


def _md_block(node: Node | str) -> list[str]:
    if isinstance(node, str):
        text = _flat(_md_inline(node))
        return [text] if text else []
    if node.tag in ("h3", "h4"):
        return ["#" * (3 if node.tag == "h3" else 4) + " " + _flat(_md_inline(node))]
    if node.tag in ("ul", "ol"):
        return _md_list(node, 0)
    if node.tag == "table":
        return _md_table(node)
    if node.tag == "pre":
        return ["```", text_of(node).strip("\n"), "```"]
    if node.tag == "blockquote":
        return ["> " + _flat(_md_inline(node))]
    if node.tag == "hr":
        return ["---"]
    if node.tag == "div" and any(
        isinstance(c, Node) and c.tag in _MD_BLOCKS for c in node.children
    ):
        return [line for child in node.children for line in _md_block(child)]
    text = _flat(_md_inline(node))
    return [text] if text else []


_MD_BLOCKS: Final = frozenset("h3 h4 p ul ol table pre blockquote hr div".split())


def to_markdown(clean: CleanHtml, owner_heading: str | None = None) -> str:
    blocks: list[list[str]] = [] if not owner_heading else [["## " + _md_escape(owner_heading)]]
    blocks += [b for b in (_md_block(child) for child in tree(clean).children) if b]
    return "\n\n".join("\n".join(block) for block in blocks).strip() + "\n"


# ---- 초안 → 형식(서비스가 마스킹한 초안을 넘긴다) ----
def draft_clean_html(masked: WeeklyDraft, template: WikiTemplate) -> CleanHtml:
    """마스킹한 초안의 본문(편집기 HTML 또는 목록으로 만든 HTML)을 CleanHtml로."""
    body = (
        masked.html
        if masked.html is not None
        else render_body(masked, with_layout(template, masked.layout))
    )
    return clean_html(body)


def export_draft(
    masked: WeeklyDraft, template: WikiTemplate, author: str, fmt: ExportFormat
) -> tuple[str, str | None]:
    """(text/plain, text/html 또는 None). masked는 `_masked`를 거친 초안이어야 한다."""
    owner = with_layout(template, masked.layout).heading(author)
    clean = draft_clean_html(masked, template)
    if fmt is ExportFormat.WIKI:
        html_text = render_section(masked, with_layout(template, masked.layout), author)
        return to_platform_text(masked, template, author, Platform.WIKI), html_text
    if fmt is ExportFormat.WIKI_SOURCE:
        return to_storage_xhtml(clean, owner), None
    if fmt is ExportFormat.SLACK:
        return to_platform_text(masked, template, author, Platform.SLACK), to_slack_html(
            clean, owner
        )
    if fmt is ExportFormat.MARKDOWN:
        return to_markdown(clean, owner), None
    plain = to_platform_text(masked, template, author, Platform.WEB)
    if fmt is ExportFormat.TEAMS:
        return plain, to_teams_html(clean, owner)
    if fmt is ExportFormat.MAIL:
        return plain, to_mail_html(clean, owner)
    return plain, None
