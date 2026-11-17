"""주간보고 HTML의 style 속성 허용 목록(C1, WIKI-02, 결정기록 0041 D1).

허용 속성과 값만 남긴다. 그 밖의 속성, `url(`, `expression`, `@import`, 역슬래시(CSS 이스케이프),
주석은 그 선언째 버린다. 순수 함수이며 외부 자원을 읽지 않는다.
"""

from __future__ import annotations

import re
from typing import Final

MAX_STYLE_CHARS: Final = 500
_COLOR: Final = re.compile(
    r"^(#[0-9a-f]{3,8}"
    r"|rgba?\(\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*(,\s*(0|1|0?\.\d{1,3})\s*)?\)"
    r"|[a-z]{3,20})$"
)
_LENGTH: Final = re.compile(r"^\d{1,4}(\.\d{1,2})?(px|%)$")
_WORDS: Final[dict[str, frozenset[str]]] = {
    "text-align": frozenset({"left", "center", "right", "justify"}),
    "font-weight": frozenset(
        {"normal", "bold", "bolder", "lighter", *map(str, range(100, 1000, 100))}
    ),
    "font-style": frozenset({"normal", "italic"}),
    "text-decoration": frozenset({"none", "underline", "line-through"}),
    "vertical-align": frozenset({"top", "middle", "bottom", "baseline"}),
}
_BANNED: Final = ("url(", "expression", "@import", "\\", "/*", "javascript:")
STYLE_PROPS: Final = frozenset({"color", "background-color", "width", *_WORDS})


def _value_ok(prop: str, value: str) -> bool:
    if prop in ("color", "background-color"):
        return bool(_COLOR.match(value))
    if prop == "width":
        return bool(_LENGTH.match(value))
    return value in _WORDS.get(prop, frozenset())


def clean_style(raw: str) -> tuple[str, int]:
    """(남긴 style 문자열, 버린 선언 수). 남길 것이 없으면 빈 문자열."""
    text = raw.strip()
    if not text:
        return "", 0
    if len(text) > MAX_STYLE_CHARS or any(word in text.lower() for word in _BANNED):
        return "", max(1, text.count(";") + 1)
    kept: list[str] = []
    dropped = 0
    for part in text.split(";"):
        if not part.strip():
            continue
        prop, sep, value = part.partition(":")
        name = prop.strip().lower()
        val = " ".join(value.strip().lower().split())
        if sep and name in STYLE_PROPS and _value_ok(name, val):
            kept.append(f"{name}: {val}")
        else:
            dropped += 1
    return "; ".join(kept), dropped
