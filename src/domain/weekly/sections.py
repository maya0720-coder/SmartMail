"""위키 페이지에서 본인 영역만 찾아 바꾸기(WIKI-04, 보안 검토 05~12 H-1·L-5).

본인 영역은 '지정 제목의 h2'부터 다음 중 먼저 나오는 곳 직전까지다.
- 같은 부모 안에서 다음 h1·h2
- 부모 요소가 닫히는 곳(Confluence 레이아웃 칸 등)

정규식 역추적 없이 태그를 한 번만 훑는다(선형 시간). 바꾼 뒤에는 본인 영역 밖의 내용이
바이트 단위로 그대로인지 다시 확인하고, 다르면 거부한다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from domain.errors import UserInputError

# 태그 하나(속성 길이 상한으로 역추적 폭발 방지)
_TAG = re.compile(r"<(/?)([A-Za-z][\w:.-]{0,40})[^<>]{0,4000}?(/?)>")
_STRIP = re.compile(r"<[^<>]{0,4000}>")
_VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "col", "area", "base", "wbr"})
_BOUNDARY = frozenset({"h1", "h2"})


class OwnSectionError(UserInputError):
    """페이지에서 본인 영역을 안전하게 특정하지 못함."""


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int


def _text(fragment: str) -> str:
    return html.unescape(_STRIP.sub("", fragment)).strip()


def _headings(body: str, heading: str) -> list[Span]:
    """제목 텍스트(안쪽 태그 제거)가 heading과 같은 h2의 [시작, 닫는 태그 끝)."""
    found = []
    for match in _TAG.finditer(body):
        if match.group(1) or match.group(2).lower() != "h2":
            continue
        close = body.lower().find("</h2>", match.end())
        if close < 0:
            continue
        if _text(body[match.end() : close]) == heading:
            found.append(Span(match.start(), close + len("</h2>")))
    return found


def _section_end(body: str, after: int) -> int:
    depth = 0
    for match in _TAG.finditer(body, after):
        closing, name, self_closing = match.group(1), match.group(2).lower(), match.group(3)
        if closing:
            if depth == 0:
                return match.start()  # 부모 요소 종료 → 영역 끝
            depth -= 1
        elif depth == 0 and name in _BOUNDARY:
            return match.start()
        elif not self_closing and name not in _VOID:
            depth += 1
    return len(body)


def find_own_section(body: str, heading: str) -> Span | None:
    spans = _headings(body, heading)
    if len(spans) > 1:
        raise OwnSectionError("본인 영역 제목이 페이지에 여러 개 있어 갱신하지 않았습니다")
    if not spans:
        return None
    return Span(spans[0].start, _section_end(body, spans[0].end))


def current_section_text(body: str, heading: str) -> str | None:
    """미리보기에 보여줄 '지금 바뀔 기존 영역' 텍스트."""
    span = find_own_section(body, heading)
    return None if span is None else _text(body[span.start : span.end])[:2000]


def replace_own_section(body: str, heading: str, new_section: str) -> str:
    """본인 영역만 바꾼다. 없으면 본문 끝에 덧붙인다."""
    span = find_own_section(body, heading)
    if span is None:
        result = body + new_section
        prefix, suffix = body, ""
    else:
        result = body[: span.start] + new_section + body[span.end :]
        prefix, suffix = body[: span.start], body[span.end :]
    # 본인 영역 밖은 그대로여야 한다(이중 확인).
    if not (result.startswith(prefix) and result.endswith(suffix)):
        raise OwnSectionError("본인 영역 밖의 내용이 바뀌어 게시를 중단했습니다")
    return result
