"""AI-Ground 화면 자동화 실패 진단(결정기록 0026).

실패하면 '어느 단계에서, 어떤 종류의 오류로, 화면이 어떤 상태였는지'만 남긴다.
질문·답변 글자, 화면 글자, 쿼리 문자열, 쿠키는 남기지 않는다(결정기록 0010·0020과 같은 기준).
"""

from __future__ import annotations

import re
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from security.site_login import safe_path

MAX_ERROR_CHARS: Final = 160
# Playwright 오류 첫 줄에서 남길 수 있는 표현만 고른다(선택자·대기 사유). 그 밖의 글자는 버린다.
_ERROR_HINTS: Final = re.compile(
    r"(Timeout \d+ms exceeded|not visible|not enabled|detached|intercepts pointer events|"
    r"strict mode violation|Target (?:page, context or browser )?(?:has been )?closed|"
    r"net::ERR_[A-Z_]+|Navigation failed|frame was detached)",
    re.IGNORECASE,
)


class _Fields(Protocol):
    @property
    def new_chat(self) -> str: ...

    @property
    def prompt(self) -> str: ...

    @property
    def submit(self) -> str: ...

    @property
    def answer(self) -> str: ...

    @property
    def done(self) -> str: ...


def error_hint(exc: BaseException) -> str:
    """오류 종류 + 알려진 대기 사유만(원문 메시지는 남기지 않는다)."""
    hints = sorted({m.group(0) for m in _ERROR_HINTS.finditer(str(exc)[:2000])})
    return (type(exc).__name__ + (": " + ", ".join(hints) if hints else ""))[:MAX_ERROR_CHARS]


def _selector_state(page: Any, selector: str) -> str:
    if not selector:
        return "-"
    try:
        loc = page.locator(selector)
        count = int(loc.count())
        if count == 0:
            return "0"
        visible = bool(loc.first.is_visible())
        enabled = bool(loc.first.is_enabled()) if visible else False
        return f"{count}{'v' if visible else 'h'}{'' if enabled or not visible else 'd'}"
    except Exception:
        return "?"


def describe_chat_page(page: Any, fields: _Fields, expected_host: str) -> str:
    """화면 상태 요약. 예: host=same path=/ new_chat=1h prompt=1v submit=1vd answer=0 done=0.

    값 뜻: 개수 + v(보임)/h(숨김) + d(보이지만 비활성). host는 같은 사이트인지 여부와
    다르면 호스트 이름만(SSO 로그인 화면으로 넘어갔는지 확인용).
    """
    try:
        parts = urlsplit(str(page.url))
        host = "same" if parts.netloc == expected_host else (parts.netloc or "-")[:60]
        path = safe_path(parts.path)
    except Exception:
        host, path = "?", "?"
    states = " ".join(
        f"{name}={_selector_state(page, sel)}"
        for name, sel in (
            ("new_chat", fields.new_chat),
            ("prompt", fields.prompt),
            ("submit", fields.submit),
            ("answer", fields.answer),
            ("done", fields.done),
        )
    )
    return f"host={host} path={path} {states}"


def _shape_char(ch: str) -> str:
    if "가" <= ch <= "힣" or "ㄱ" <= ch <= "ㆎ":
        return "가"
    if ch.isascii() and ch.isalpha():
        return "a"
    if ch.isdigit():
        return "9"
    return ch if ch in " []()*#:：.-/>|" else "_"


def answer_shape(text: str, lines: int = 8, width: int = 24) -> str:
    """해석 실패 진단용 답변 '모양'. 글자를 종류로만 바꾼다(한글→가, 영문→a, 숫자→9).

    내용은 남지 않고 줄 구조·기호(대괄호·콜론·별표 등)만 남는다.
    """
    rows = [line for line in text.splitlines() if line.strip()][:lines]
    out = ["".join(_shape_char(c) for c in row[:width]) for row in rows]
    return f"lines={len(text.splitlines())} " + " ¶ ".join(out)
