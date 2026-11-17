"""주간보고 미리보기 편집기 HTML 정리·마스킹(WIKI-02·03·04, 결정기록 0030).

- 표준 라이브러리 `html.parser`로 한 번 훑으며 허용 목록 태그·속성만 남긴다(새 의존성 없음).
  허용 태그: h1 h2 h3 h4 p br hr ul ol li strong b em i u s sub sup code pre blockquote span div
    table caption colgroup col thead tbody tfoot tr th td a (HTML 편집 확장, 결정기록 0041 D1)
  허용 속성: a[href](https, 허용 사이트만 — security.links.safe_link), th/td[colspan·rowspan](1~20),
    th/td[align·valign·width], col[span·width], ol[start], style(허용 속성·값만 — rich_style.py)
- 버린 태그·속성은 종류별 개수로 요약한다(`sanitize_report`, 화면의 '제거됨: script 1 …').
- script·style·iframe 등은 내용째 버리고, 그 밖의 허용되지 않은 태그·속성·주석은 태그만 버리고
  글자는 남긴다. 표 안의 표(중첩 표)는 표 태그만 버린다.
- 본인 영역 경계(h1·h2, `sections.py`)를 깨지 않도록 본문의 h1·h2는 h3으로 낮춘다.
- 글자 노드마다 마스킹한 뒤 이스케이프한다. 노드 사이에 걸친 민감정보는 `unmasked_text_left`로
  다시 확인해 게시 전에 거부한다.
- 입력은 200KB 이하만 받는다. 출력은 Confluence 저장 형식과 호환되는 XHTML(`<br />`·`<hr />`)이다.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final

from domain.errors import UserInputError
from domain.weekly.rich_style import clean_style
from security.links import safe_link
from security.masking import mask

MAX_HTML_BYTES: Final = 200_000
MAX_DEPTH: Final = 60
MAX_SPAN: Final = 20
ALLOWED_TAGS: Final = frozenset(
    "h1 h2 h3 h4 p br hr ul ol li strong b em i u s sub sup code pre blockquote span div"
    " table caption colgroup col thead tbody tfoot tr th td a".split()
)
VOID_TAGS: Final = frozenset({"br", "hr", "col"})
_ALIGN: Final = frozenset({"left", "center", "right", "justify"})
_VALIGN: Final = frozenset({"top", "middle", "bottom", "baseline"})
_WIDTH: Final = re.compile(r"^\d{1,4}(px|%)?$")
MAX_OL_START: Final = 9999
# 내용까지 버리는 태그(글자로도 남기지 않음)
DROP_WITH_CONTENT: Final = frozenset(
    "script style iframe object embed noscript template svg math head title textarea select"
    " option button form frame frameset applet".split()
)
BLOCK_TAGS: Final = frozenset(
    "h3 h4 p br hr li tr th td table ul ol pre blockquote div caption".split()
)
TABLE_TAGS: Final = frozenset("table caption colgroup col thead tbody tfoot tr th td".split())


class RichHtmlError(UserInputError):
    """편집 내용을 받을 수 없음(크기 초과 등). 메시지는 사용자 안내용."""


_CLEAN_KEY: Final = object()


@dataclass(frozen=True, slots=True)
class CleanHtml:
    """정리·마스킹을 거친 HTML. `clean_html()`만 만든다(플랫폼 변환기는 이 타입만 받는다)."""

    value: str
    key: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self.key is not _CLEAN_KEY:
            raise TypeError("정리·마스킹된 HTML만 쓸 수 있습니다(clean_html 사용)")


@dataclass
class _State:
    out: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)  # 마스킹 확인용 글자(블록 경계는 줄바꿈)
    stack: list[str] = field(default_factory=list)
    skip: int = 0  # 내용째 버리는 태그 깊이
    tables: int = 0  # 열린 표 수(중첩 표 판별)
    nested: int = 0  # 버린 중첩 표 태그 깊이
    removed: Counter[str] = field(default_factory=Counter)  # 버린 태그·속성 종류별 개수


class _Sanitizer(HTMLParser):
    def __init__(self, link_bases: Iterable[str], apply_mask: bool) -> None:
        super().__init__(convert_charrefs=True)
        self._bases = tuple(link_bases)
        self._mask = apply_mask
        self.state = _State()

    # ---- 태그 ----
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        state = self.state
        if tag in DROP_WITH_CONTENT:
            if not state.skip:
                state.removed[tag] += 1
            state.skip += 1
            return
        if state.skip:
            return
        if tag not in ALLOWED_TAGS or len(state.stack) >= MAX_DEPTH:
            state.removed[tag if tag not in ALLOWED_TAGS else "깊은 중첩"] += 1
            return
        if tag == "table" and state.tables:
            state.nested += 1  # 중첩 표는 태그만 버린다
            state.removed["중첩 표"] += 1
            return
        if state.nested and tag in TABLE_TAGS:
            return
        name = "h3" if tag in ("h1", "h2") else tag
        if name in BLOCK_TAGS:
            state.text.append("\n")
        if name in VOID_TAGS:
            state.out.append(f"<{name}{self._attrs(name, attrs)} />")
            return
        if name == "table":
            state.tables += 1
        state.out.append(f"<{name}{self._attrs(name, attrs)}>")
        state.stack.append(name)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        state = self.state
        if tag in DROP_WITH_CONTENT:
            state.skip = max(0, state.skip - 1)
            return
        if state.skip or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag == "table" and state.nested:
            state.nested -= 1
            return
        if state.nested and tag in TABLE_TAGS:
            return
        name = "h3" if tag in ("h1", "h2") else tag
        if name not in state.stack:
            return  # 열리지 않은 닫는 태그는 버린다
        while state.stack:
            opened = state.stack.pop()
            if opened == "table":
                state.tables -= 1
            state.out.append(f"</{opened}>")
            if opened == name:
                break

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        kept: list[str] = []
        removed = self.state.removed
        for key, raw in attrs:
            value = (raw or "").strip()
            if key == "style":
                style, dropped = clean_style(value)
                if style:
                    kept.append(f' style="{html.escape(style, quote=True)}"')
                if dropped:
                    removed["style 값" if style else "style 속성"] += 1
                continue
            attr = self._attr(tag, key, value)
            if attr is None:
                removed["링크(허용되지 않은 주소)" if key == "href" else f"{key} 속성"] += 1
            else:
                kept.append(attr)
        return "".join(kept)

    def _attr(self, tag: str, key: str, value: str) -> str | None:
        """허용 속성 하나(` 이름="값"`) 또는 None(버림)."""
        number = int(value) if value.isdigit() and len(value) <= 5 else 0
        if tag == "a" and key == "href":
            link = safe_link(value, self._bases)
            return None if link is None else f' href="{html.escape(link, quote=True)}"'
        if tag in ("th", "td", "col") and key in ("colspan", "rowspan", "span"):
            ok = (key != "span" or tag == "col") and (key == "span" or tag != "col")
            return f' {key}="{number}"' if ok and 1 <= number <= MAX_SPAN else None
        if tag in ("th", "td") and key == "align" and value.lower() in _ALIGN:
            return f' align="{value.lower()}"'
        if tag in ("th", "td") and key == "valign" and value.lower() in _VALIGN:
            return f' valign="{value.lower()}"'
        if tag in ("th", "td", "col") and key == "width" and _WIDTH.match(value):
            return f' width="{value}"'
        if tag == "ol" and key == "start" and 1 <= number <= MAX_OL_START:
            return f' start="{number}"'
        return None

    # ---- 글자 ----
    def handle_data(self, data: str) -> None:
        state = self.state
        if state.skip or not data:
            return
        clean = "".join(ch for ch in data if ch.isprintable() or ch in "\n\t ")
        text = mask(clean).value if self._mask else clean
        state.text.append(text)
        state.out.append(html.escape(text, quote=False))

    # 주석·선언·처리 명령(CDATA 포함)은 모두 버린다
    def handle_comment(self, data: str) -> None:
        self.state.removed["주석"] += 1

    def handle_decl(self, decl: str) -> None:
        return None

    def handle_pi(self, data: str) -> None:
        self.state.removed["처리 명령"] += 1

    def unknown_decl(self, data: str) -> None:
        self.state.removed["CDATA"] += 1

    def finish(self) -> str:
        self.close()
        while self.state.stack:
            self.state.out.append(f"</{self.state.stack.pop()}>")
        return "".join(self.state.out)


def _check_size(raw: str) -> None:
    if len(raw.encode("utf-8")) > MAX_HTML_BYTES:
        raise RichHtmlError("편집 내용이 너무 큽니다(200KB 이하)")


def sanitize_report(
    raw: str, link_bases: Iterable[str] = (), *, apply_mask: bool = True
) -> tuple[str, dict[str, int]]:
    """(정리·마스킹한 HTML, 버린 태그·속성 종류별 개수). 크기를 넘으면 RichHtmlError."""
    _check_size(raw)
    parser = _Sanitizer(link_bases, apply_mask)
    parser.feed(raw)
    result = parser.finish()
    _check_size(result)
    return result, dict(parser.state.removed)


def sanitize(raw: str, link_bases: Iterable[str] = (), *, apply_mask: bool = True) -> str:
    """허용 목록으로 정리하고 글자를 마스킹한 HTML. 크기를 넘으면 RichHtmlError."""
    return sanitize_report(raw, link_bases, apply_mask=apply_mask)[0]


def clean_html(raw: str, link_bases: Iterable[str] = ()) -> CleanHtml:
    """정리·마스킹한 HTML을 변환기용 타입으로. 서식 사이에 걸친 민감정보가 남으면 거부한다."""
    value = sanitize(raw, link_bases)
    if unmasked_text_left(value):
        raise UserInputError(
            "서식 사이에 걸친 민감정보(전화번호·계좌번호 등)가 있어 받을 수 없습니다. "
            "해당 글자를 고쳐 주세요"
        )
    return CleanHtml(value, _CLEAN_KEY)


def plain_text(clean_html: str) -> str:
    """정리된 HTML의 글자(블록 경계는 줄바꿈). 마스킹 재확인·지문용."""
    parser = _Sanitizer((), apply_mask=False)
    parser.feed(clean_html)
    parser.finish()
    return "".join(parser.state.text)


def unmasked_text_left(clean_html: str) -> bool:
    """글자 노드 사이에 걸쳐 마스킹을 피한 민감정보가 남아 있는지(블록 안 글자를 이어 붙여 확인)."""
    text = plain_text(clean_html)
    return mask(text).value != text


_BLOCK_KINDS: Final = {
    "h3": "heading",
    "h4": "heading",
    "p": "para",
    "li": "item",
    "pre": "para",
    "blockquote": "para",
    "div": "para",
    "caption": "para",
}


class _Blocks(HTMLParser):
    """정리된 HTML → (종류, 글자) 블록. 종류: heading | item | para | row."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._kind: str | None = None
        self._buf: list[str] = []
        self._cells: list[str] | None = None

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        if self._kind is not None and text:
            if self._kind == "cell" and self._cells is not None:
                self._cells.append(text)
            else:
                self.blocks.append((self._kind, text))
        self._kind, self._buf = None, []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_KINDS:
            self._flush()
            self._kind = _BLOCK_KINDS[tag]
        elif tag == "tr":
            self._flush()
            self._cells = []
        elif tag in ("th", "td"):
            self._flush()
            self._kind = "cell"
        elif tag == "br":
            self._buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_KINDS or tag in ("th", "td"):
            self._flush()
        elif tag == "tr":
            self._flush()
            if self._cells:
                self.blocks.append(("row", " | ".join(self._cells)))
            self._cells = None

    def handle_data(self, data: str) -> None:
        if self._kind is None:
            self._kind = "para"
        self._buf.append(data)


def blocks(clean_html: str) -> list[tuple[str, str]]:
    parser = _Blocks()
    parser.feed(clean_html)
    parser.close()
    parser._flush()
    return parser.blocks
