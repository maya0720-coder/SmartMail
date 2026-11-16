"""위키 페이지 본문 읽기 어댑터 테스트(N4, WIKI-02, 결정기록 0037 보안 검토 반영).

가짜 브라우저 페이지만 쓴다. 실제 위키에는 접속하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from domain.clock import FixedClock
from domain.weekly.service import WikiLoginRequiredError, WikiUnavailableError
from fixtures.app_factory import FakeBackend
from integrations.wiki.reader import (
    FOREIGN_LINK,
    TOO_LARGE,
    BrowserWikiReader,
    WikiReadRules,
)
from security.browser import BrowserRuntime
from security.logging_policy import Site
from security.sessions import SessionStore

BASE = "https://wiki.example.invalid"
PAGE = BASE + "/pages/1"


class _Element:
    def __init__(self, page: _Page) -> None:
        self._page = page

    def evaluate(self, script: str, limit: int) -> str | None:
        self._page.evaluated = True
        body = self._page.body
        return None if len(body) > limit else body  # 브라우저 안 길이 검사 흉내


class _Page:
    def __init__(self, landing: str, body: str, *, moved_after_wait: str | None = None) -> None:
        self.url = "about:blank"
        self.body = body
        self._landing = landing
        self._moved = moved_after_wait
        self.evaluated = False
        self.closed = False

    def goto(self, url: str, wait_until: str, timeout: int) -> None:
        self.url = self._landing

    def wait_for_selector(self, selector: str, timeout: int) -> _Element:
        if self._moved is not None:
            self.url = self._moved  # 기다리는 동안 다른 주소로 이동
        return _Element(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def runtime() -> Iterator[BrowserRuntime]:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    yield runtime
    runtime.shutdown()


def _reader(
    clock: FixedClock, runtime: BrowserRuntime, page: _Page, max_bytes: int = 1000
) -> BrowserWikiReader:
    context = runtime.open_context()
    raw: Any = context._raw
    raw.new_page = lambda: page
    sessions = SessionStore(clock, timedelta(minutes=30))
    sessions.open(Site.WIKI, context)
    return BrowserWikiReader(BASE, sessions, WikiReadRules("#main-content", max_bytes, 5.0))


def test_wiki02_reader_reads_body_in_base(clock: FixedClock, runtime: BrowserRuntime) -> None:
    page = _Page(PAGE, "<h2>합성</h2>")
    assert _reader(clock, runtime, page).read_page_html(PAGE) == "<h2>합성</h2>"
    assert page.closed


def test_wiki02_reader_rejects_redirect_before_and_after_wait(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    away = _Page("https://evil.example.invalid/x", "<p>x</p>")
    with pytest.raises(WikiUnavailableError, match=FOREIGN_LINK):
        _reader(clock, runtime, away).read_page_html(PAGE)
    late = _Page(PAGE, "<p>x</p>", moved_after_wait="https://evil.example.invalid/y")
    with pytest.raises(WikiUnavailableError, match=FOREIGN_LINK):
        _reader(clock, runtime, late).read_page_html(PAGE)
    assert not late.evaluated and late.closed  # 본문을 읽기 전에 멈춘다


def test_wiki02_reader_size_checked_in_browser(clock: FixedClock, runtime: BrowserRuntime) -> None:
    big = _Page(PAGE, "가" * 2000)
    with pytest.raises(WikiUnavailableError, match=re.escape(TOO_LARGE)):
        _reader(clock, runtime, big, max_bytes=1000).read_page_html(PAGE)
    # 글자 수는 상한 안이지만 바이트(한글 3바이트)로는 넘는 경우도 거부
    wide = _Page(PAGE, "가" * 400)
    with pytest.raises(WikiUnavailableError, match=re.escape(TOO_LARGE)):
        _reader(clock, runtime, wide, max_bytes=1000).read_page_html(PAGE)


def test_wiki02_reader_login_and_foreign_link(clock: FixedClock, runtime: BrowserRuntime) -> None:
    login = _Page(BASE + "/login.action", "")
    with pytest.raises(WikiLoginRequiredError):
        _reader(clock, runtime, login).read_page_html(PAGE)
    page = _Page(PAGE, "")
    with pytest.raises(WikiUnavailableError, match=FOREIGN_LINK):
        _reader(clock, runtime, page).read_page_html("https://evil.example.invalid/pages/1")
    no_session = BrowserWikiReader(
        BASE, SessionStore(clock, timedelta(minutes=30)), WikiReadRules()
    )
    assert not no_session.has_session()
    with pytest.raises(WikiLoginRequiredError):
        no_session.read_page_html(PAGE)
