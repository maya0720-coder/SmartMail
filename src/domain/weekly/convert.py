"""대상 플랫폼별 주간보고 텍스트 변환(WIKI-05). 입력은 이미 마스킹된 초안이어야 한다.

- slack: mrkdwn(*굵게*, • 목록). `&<>`는 슬랙 규칙대로 치환하고 전체 멘션은 무력화한다.
- wiki: Confluence 위키 마크업(h2./h3./*). 서식 기호는 백슬래시로 이스케이프한다.
- web·teams·mail: 일반 텍스트. markdown: '### 머리글'·'- 항목'(전체 변환은 convert_formats.py).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from domain.weekly.links import Platform
from domain.weekly.report import WeeklyDraft, WikiTemplate, single_line, with_layout
from domain.weekly.rich import blocks

_MENTION = re.compile(r"@(channel|here|everyone)", re.IGNORECASE)
_WIKI_SPECIAL = re.compile(r"([\\{}\[\]|*_^~!+?\-#])")
_BULLET = "•"


def _slack(text: str) -> str:
    safe = single_line(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MENTION.sub("@​\\1", safe)


def _wiki(text: str) -> str:
    return _WIKI_SPECIAL.sub(r"\\\1", single_line(text))


def _plain(text: str) -> str:
    return text


def _slack_head(text: str) -> str:
    return f"*{_slack(text)}*"


def _slack_item(text: str) -> str:
    return f"{_BULLET} {_slack(text)}"


def _wiki_head(text: str) -> str:
    return f"h3. {_wiki(text)}"


def _wiki_item(text: str) -> str:
    return f"* {_wiki(text)}"


def _plain_item(text: str) -> str:
    return f"- {single_line(text)}"


def _md_head(text: str) -> str:
    return f"### {single_line(text)}"


# 플랫폼별 (머리글, 항목, 빈 영역 표시) 서식
_STYLES: dict[Platform, tuple[Callable[[str], str], Callable[[str], str], str]] = {
    Platform.SLACK: (_slack_head, _slack_item, f"{_BULLET} -"),
    Platform.WIKI: (_wiki_head, _wiki_item, "* -"),
    Platform.WEB: (_plain, _plain_item, "- -"),
    Platform.TEAMS: (_plain, _plain_item, "- -"),
    Platform.MAIL: (_plain, _plain_item, "- -"),
    Platform.MARKDOWN: (_md_head, _plain_item, "- -"),
}


def to_platform_text(
    draft: WeeklyDraft,
    template: WikiTemplate,
    owner: str,
    platform: Platform,
    *,
    include_owner: bool = True,
) -> str:
    """플랫폼 형식 텍스트. include_owner=False면 본인 이름 머리글을 뺀다(보관 스냅샷용)."""
    tpl = with_layout(template, draft.layout)
    sections = (
        (tpl.heading_done, draft.done),
        (tpl.heading_planned, draft.planned),
        (tpl.heading_issues, draft.issues),
    )
    lines: list[str] = []
    head, item, empty = _STYLES[platform]
    if include_owner:
        owner_line = tpl.heading(owner)
        if platform is Platform.WIKI:
            lines += [f"h2. {_wiki(owner_line)}", ""]
        else:
            lines += [head(owner_line), ""]
    if draft.html is not None:
        lines += _html_lines(draft.html, platform)
        return "\n".join(lines).rstrip() + "\n"
    for heading, items in sections:
        lines.append(head(heading))
        lines += [item(i) for i in items] or [empty]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _html_lines(clean_html: str, platform: Platform) -> list[str]:
    """편집기 본문(정리·마스킹된 HTML) → 플랫폼 줄(결정기록 0030). 표는 '칸 | 칸' 한 줄."""
    head, item, _empty = _STYLES[platform]
    plain = {Platform.SLACK: _slack, Platform.WIKI: _wiki}.get(platform, single_line)
    lines: list[str] = []
    for kind, text in blocks(clean_html):
        if kind == "heading":
            lines += ["", head(text)] if lines else [head(text)]
        elif kind == "item":
            lines.append(item(text))
        else:
            lines.append(plain(text))
    return lines
