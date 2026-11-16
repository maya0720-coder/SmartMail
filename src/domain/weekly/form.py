"""참고 링크 양식 구조 파싱(WIKI-02).

링크 양식의 목차·머리글 줄을 읽어 금주 실적(완료·진행) / 차주 계획(예정) / 이슈·요청 세 영역에
키워드 규칙으로 매핑한다. 매핑되는 머리글이 하나도 없으면 호출한 쪽이 기본 양식으로 되돌린다.
키워드는 `config/rules/weekly_form.toml`에서 바꾼다(기본값은 아래 상수).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from domain.errors import UserInputError
from security.masking import mask

MAX_OUTLINE_LINES = 30  # 양식 목차 최대 줄 수
MAX_HEADING_CHARS = 60  # 머리글 한 줄 최대 글자 수
MAX_PARSE_CHARS = 200_000  # 위키 본문 파싱 상한(과대 입력 방지)

_LEAD = re.compile(r"^\s*(?:(?:h[1-6]\.|[#>*•·=\-]+|\d{1,2}[.)])\s*)+", re.IGNORECASE)
_TAIL = re.compile(r"[\s:*_=]+$")
_HEADING_TAGS = ("h1", "h2", "h3", "h4")


@dataclass(frozen=True, slots=True)
class FormRules:
    """머리글 키워드(소문자 비교). 판정 순서는 이슈 → 계획 → 실적(첫 일치)."""

    issues: tuple[str, ...] = ("이슈", "요청", "문제", "건의", "리스크", "risk", "blocker", "issue")
    planned: tuple[str, ...] = ("차주", "예정", "계획", "다음 주", "다음주", "next", "plan")
    done: tuple[str, ...] = (
        "실적",
        "완료",
        "금주",
        "이번 주",
        "이번주",
        "진행",
        "done",
        "this week",
        "progress",
    )


@dataclass(frozen=True, slots=True)
class FormLayout:
    """주간보고 영역별 머리글. source는 'default' 또는 'link'."""

    heading_done: str
    heading_planned: str
    heading_issues: str
    source: str = "default"
    label: str = ""
    matched: int = 0

    def headings(self) -> tuple[str, str, str]:
        return (self.heading_done, self.heading_planned, self.heading_issues)


def clean_heading(raw: str) -> str:
    """번호·마크다운·위키 마크업 기호를 벗기고 길이를 제한한 뒤 마스킹한다."""
    text = _LEAD.sub("", raw.strip())
    text = _TAIL.sub("", text).strip()[:MAX_HEADING_CHARS]
    return mask(text).value if text else ""


def parse_outline(text: str) -> list[str]:
    """붙여 넣은 양식 목차(줄 단위)에서 머리글 목록을 만든다. 너무 많으면 UserInputError."""
    lines = [line for line in (clean_heading(r) for r in text.splitlines()) if line]
    if len(lines) > MAX_OUTLINE_LINES:
        raise UserInputError(f"양식 목차는 최대 {MAX_OUTLINE_LINES}줄까지 입력할 수 있습니다")
    return lines


class _HeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self._open: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HEADING_TAGS:
            self._open = []

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._open.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in _HEADING_TAGS and self._open is not None:
            heading = clean_heading("".join(self._open))
            if heading and len(self.headings) < MAX_OUTLINE_LINES:
                self.headings.append(heading)
            self._open = None


def headings_from_html(body: str) -> list[str]:
    """위키 저장 형식(HTML) 본문에서 h1~h4 머리글만 뽑는다(스크립트는 실행·보관하지 않음)."""
    parser = _HeadingParser()
    parser.feed(body[:MAX_PARSE_CHARS])
    parser.close()
    return parser.headings


def classify_heading(heading: str, rules: FormRules) -> str | None:
    """머리글을 'issues'·'planned'·'done' 중 하나로 매핑한다. 해당 없으면 None."""
    lowered = heading.lower()
    for section, words in (
        ("issues", rules.issues),
        ("planned", rules.planned),
        ("done", rules.done),
    ):
        if any(word.lower() in lowered for word in words):
            return section
    return None


def build_layout(
    headings: list[str],
    defaults: tuple[str, str, str],
    rules: FormRules,
    label: str,
) -> FormLayout | None:
    """머리글을 영역에 매핑한다. 하나도 매핑되지 않으면 None(→ 기본 양식)."""
    chosen: dict[str, str] = {}
    for heading in headings:
        section = classify_heading(heading, rules)
        if section is not None and section not in chosen:
            chosen[section] = heading
    if not chosen:
        return None
    return FormLayout(
        chosen.get("done", defaults[0]),
        chosen.get("planned", defaults[1]),
        chosen.get("issues", defaults[2]),
        "link",
        label,
        len(chosen),
    )


def layout_from_client(done: str, planned: str, issues: str) -> FormLayout:
    """화면이 돌려보낸 머리글을 다시 검증한다(길이·마스킹). 비어 있으면 UserInputError."""
    values = tuple(clean_heading(v) for v in (done, planned, issues))
    if not all(values):
        raise UserInputError("양식 머리글이 올바르지 않습니다")
    return FormLayout(values[0], values[1], values[2], "link")
