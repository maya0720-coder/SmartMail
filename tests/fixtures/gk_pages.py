"""G-Knowledge 브라우저 어댑터 테스트용 가짜 페이지(실제 사이트에 접속하지 않음)."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from domain.clock import FixedClock
from fixtures.app_factory import FakeBackend
from integrations.gknowledge.browser import BrowserKnowledgeStore, RegisterFields, SearchFields
from security.browser import BrowserRuntime
from security.logging_policy import Site
from security.sessions import SessionStore


class FakePage:
    def __init__(self, log: list[tuple[str, ...]], result: str) -> None:
        self.log = log
        self.url = result
        self.items: list[Any] = []
        self.filled: dict[str, str] = {}

    def goto(self, url: str, timeout: int) -> None:
        self.log.append(("goto", url))

    def fill(self, selector: str, value: str) -> None:
        self.log.append(("fill", selector, value))
        self.filled[selector] = value

    def inner_text(self, selector: str) -> str:
        return self.filled.get(selector, "")

    def click(self, selector: str) -> None:
        self.log.append(("click", selector))

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        return None

    def wait_for_url(self, predicate: Any, timeout: int) -> None:
        if not predicate(self.url):
            raise TimeoutError("주소가 바뀌지 않음")

    def wait_for_selector(self, selector: str, timeout: int) -> None:
        if not self.items and selector == ".item":
            raise TimeoutError("결과 없음")

    def wait_for_function(self, expression: str, arg: Any = None, timeout: int = 0) -> None:
        self.log.append(("wait_filled", str(arg)))

    def query_selector_all(self, selector: str) -> list[Any]:
        return self.items

    def get_attribute(self, selector: str, name: str) -> str:
        return self.url

    def close(self) -> None:
        self.log.append(("close",))


def browser_store(
    clock: FixedClock,
    result: str,
    now: Any = time.monotonic,
    page_cls: Any = None,
    fields: RegisterFields | None = None,
) -> tuple[BrowserKnowledgeStore, list[Any], BrowserRuntime]:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    sessions = SessionStore(clock, timedelta(minutes=30))
    context = runtime.open_context()
    log: list[tuple[str, ...]] = []
    cls = page_cls or FakePage
    context._raw.new_page = lambda: cls(log, result)  # type: ignore[union-attr]
    sessions.open(Site.GKNOWLEDGE, context)
    fields = fields or RegisterFields(
        "/knowledge/new", "#t", "#b", "", "#s", "", "/knowledge/[0-9]+"
    )
    empty = SearchFields("", "", "", "", "", "")
    return (
        BrowserKnowledgeStore("https://gk.example.invalid", sessions, fields, empty, now=now),
        log,
        runtime,
    )
