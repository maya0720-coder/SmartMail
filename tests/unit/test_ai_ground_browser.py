"""AI-Ground 채팅 화면 자동화 어댑터 테스트(AI-01·04·05, 결정기록 0008).

가짜 브라우저 페이지와 가짜 시계만 쓴다. 실제 AI-Ground에는 접속하지 않는다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from config.settings import ConfigError
from domain.ai import AiUnavailableError
from domain.clock import FixedClock
from fixtures.app_factory import FakeBackend, app_client, make_container
from integrations.ai.ground_browser import (
    ANSWER_TIMEOUT,
    MAX_CALLS_PER_WINDOW,
    MIN_INTERVAL_SECONDS,
    NOT_SENT,
    BrowserGllmAdapter,
    ChatFields,
    load_chat_fields,
)
from integrations.ai.prompts import parse_answer, parse_classification
from security.browser import BrowserRuntime
from security.logging_policy import Site
from security.masking import mask
from security.sessions import SessionStore

BASE = "https://ai.example.invalid"
CHAT = ChatFields("/chat", "", "", "", "#q", "", ".answer", ".stop", "")


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []
        self.on_sleep: list[Any] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds
        for hook in self.on_sleep:
            hook()


class FakeLocator:
    def __init__(self, page: FakeChatPage, selector: str) -> None:
        self._page = page
        self._selector = selector

    def _items(self) -> list[str]:
        if self._selector == ".answer":
            return self._page.answers
        if self._selector == ".stop":
            return ["stop"] if self._page.streaming else []
        return []

    def count(self) -> int:
        return len(self._items())

    def nth(self, index: int) -> FakeLocator:
        item = FakeLocator(self._page, self._selector)
        item._index = index  # type: ignore[attr-defined]
        return item

    def inner_text(self) -> str:
        return self._items()[self._index]  # type: ignore[attr-defined]


class FakeChatPage:
    """보내면 답변이 조각(chunk)으로 늘어나는 스트리밍 화면."""

    def __init__(self, clock: FakeClock, chunks: list[str], endless: bool = False) -> None:
        self.log: list[tuple[str, ...]] = []
        self.answers = ["이전 대화 답변"]
        self.streaming = False
        self._chunks = list(chunks)
        self._endless = endless
        clock.on_sleep.append(self._tick)

    def _tick(self) -> None:
        if not self.streaming:
            return
        if self._chunks:
            self.answers[-1] += self._chunks.pop(0)
        elif self._endless:
            self.answers[-1] += "."
        else:
            self.streaming = False

    def set_default_timeout(self, timeout: int) -> None:
        self.log.append(("timeout", str(timeout)))

    def goto(self, url: str, timeout: int) -> None:
        self.log.append(("goto", url))

    def click(self, selector: str) -> None:
        self.log.append(("click", selector))

    def fill(self, selector: str, value: str) -> None:
        self.log.append(("fill", selector, value))

    def press(self, selector: str, key: str) -> None:
        self.log.append(("press", selector, key))
        self.answers.append("")
        self.streaming = True

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def close(self) -> None:
        self.log.append(("close",))


@pytest.fixture
def runtime() -> Iterator[BrowserRuntime]:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    yield runtime
    runtime.shutdown()


def _adapter(
    clock: FixedClock,
    runtime: BrowserRuntime,
    chunks: list[str],
    *,
    endless: bool = False,
    chat: ChatFields = CHAT,
    timeout: float = 30.0,
) -> tuple[BrowserGllmAdapter, FakeChatPage, FakeClock]:
    fake_clock = FakeClock()
    page = FakeChatPage(fake_clock, chunks, endless)
    context = runtime.open_context()
    context._raw.new_page = lambda: page  # type: ignore[union-attr]
    sessions = SessionStore(clock, timedelta(minutes=30))
    sessions.open(Site.AIGROUND, context)
    adapter = BrowserGllmAdapter(
        BASE, sessions, chat, timeout_seconds=timeout, now=fake_clock.now, sleep=fake_clock.sleep
    )
    return adapter, page, fake_clock


def test_ai01_classify_waits_for_streaming_answer(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    adapter, page, _ = _adapter(clock, runtime, ["업무: ", "예\n모듈: fi", "\n확신도: 0.8"])
    result = adapter.classify_task(mask("전표 역분개 요청 / 확인 부탁드립니다"))
    assert result.is_task and result.module_tag == "FI" and result.confidence == 0.8
    assert ("goto", f"{BASE}/chat") in page.log
    [fill] = [entry for entry in page.log if entry[0] == "fill"]
    assert "전표 역분개 요청" in fill[2]
    assert ("press", "#q", "Enter") in page.log and page.log[-1] == ("close",)


def test_ai04_suggestion_text_and_links(clock: FixedClock, runtime: BrowserRuntime) -> None:
    answer = ["1. OB52 기간 확인\n", "2. 재전기\n근거: https://gk.example.invalid/k/1"]
    adapter, _, _ = _adapter(clock, runtime, answer)
    result = adapter.suggest_solution(mask("역분개 오류"), [mask("전기기간 닫힘 사례")])
    assert result.text.startswith("1. OB52")
    assert result.references == ("https://gk.example.invalid/k/1",)


def test_ai06_endless_stream_times_out_and_closes_page(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    adapter, page, _ = _adapter(clock, runtime, ["업무: 예"], endless=True, timeout=5)
    with pytest.raises(AiUnavailableError, match=ANSWER_TIMEOUT):
        adapter.classify_task(mask("요청"))
    assert page.log[-1] == ("close",)


def test_ai05_requires_login_and_confirmed_fields(clock: FixedClock) -> None:
    sessions = SessionStore(clock, timedelta(minutes=30))
    adapter = BrowserGllmAdapter(BASE, sessions, CHAT, timeout_seconds=10)
    with pytest.raises(AiUnavailableError, match="로그인"):
        adapter.classify_task(mask("x"))
    empty = ChatFields("", "", "", "", "", "", "", "", "")
    unready = BrowserGllmAdapter(BASE, sessions, empty, timeout_seconds=10)
    with pytest.raises(AiUnavailableError, match="확인 필요"):
        unready.suggest_solution(mask("x"), [])


def test_ai05_foreign_chat_path_rejected(clock: FixedClock, runtime: BrowserRuntime) -> None:
    chat = ChatFields("https://evil.example/chat", "", "", "", "#q", "", ".answer", "", "")
    adapter, page, _ = _adapter(clock, runtime, ["업무: 예"], chat=chat)
    with pytest.raises(AiUnavailableError, match="주소"):
        adapter.classify_task(mask("요청"))
    assert page.log == []


def test_ai06_throttle_interval_and_window_cap(clock: FixedClock, runtime: BrowserRuntime) -> None:
    adapter, page, fake_clock = _adapter(clock, runtime, [])
    adapter._throttle()
    adapter._throttle()
    assert fake_clock.sleeps == [MIN_INTERVAL_SECONDS]
    for _ in range(MAX_CALLS_PER_WINDOW - 2):
        adapter._throttle()
    with pytest.raises(AiUnavailableError, match="10분"):
        adapter._throttle()
    fake_clock.t += 601
    adapter._throttle()  # 구간이 지나면 다시 허용
    assert page.log == []


@pytest.mark.parametrize("text", ["", "잘 모르겠습니다", "업무 예"])
def test_unparseable_classification_rejected(text: str) -> None:
    with pytest.raises(AiUnavailableError):
        parse_classification(text)


def test_classification_defaults_and_unknown_module() -> None:
    result = parse_classification("**업무**: 아니오\n모듈: 회계")
    assert not result.is_task and result.module_tag is None and result.confidence == 0.5


def test_empty_answer_rejected() -> None:
    with pytest.raises(AiUnavailableError):
        parse_answer("   ")


def test_parsers_linear_on_long_input() -> None:
    started = time.perf_counter()
    parse_answer("https://" + "a" * 50_000)
    with pytest.raises(AiUnavailableError):
        parse_classification(" " * 50_000 + "업무")
    assert time.perf_counter() - started < 0.5


def test_default_chat_fields_unconfirmed(tmp_path: Path) -> None:
    assert not load_chat_fields().ready
    bad = tmp_path / "f.toml"
    bad.write_text("[chat]\nunknown = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_chat_fields(bad)


def test_bootstrap_uses_browser_adapter_without_resend(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock, demo_mode=False, ai_adapter="gllm")
    with app_client(container):
        assert isinstance(container.ai.adapter, BrowserGllmAdapter)
        assert container.ai._retries == 0  # 저장·학습되는 곳이므로 재전송하지 않음


def test_ai06_not_sent_when_dequeued_after_deadline(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    """브라우저 스레드가 다른 작업에 잡혀 포기 시각이 지나면, 나중에 꺼내져도 보내지 않는다."""
    adapter, page, fake_clock = _adapter(clock, runtime, ["업무: 예"], timeout=30)
    release = threading.Event()

    def busy() -> None:
        release.wait(5)
        fake_clock.t += 100  # 앞 작업이 포기 시각을 넘길 만큼 오래 걸림

    runtime._executor.submit(busy)
    timer = threading.Timer(0.1, release.set)
    timer.start()
    with pytest.raises(AiUnavailableError, match=NOT_SENT):
        adapter.classify_task(mask("요청"))
    assert not [entry for entry in page.log if entry[0] in ("fill", "press", "click")]


def test_ai06_long_queue_is_refused(clock: FixedClock, runtime: BrowserRuntime) -> None:
    adapter, page, fake_clock = _adapter(clock, runtime, [])
    adapter._calls.append(fake_clock.t + 10)  # 앞 호출 예약이 많이 밀려 있음
    with pytest.raises(AiUnavailableError, match=NOT_SENT):
        adapter._throttle()
    assert fake_clock.sleeps == [] and page.log == []


def test_runtime_timeout_cancels_queued_job(runtime: BrowserRuntime) -> None:
    release = threading.Event()
    ran: list[str] = []
    runtime._executor.submit(lambda: release.wait(5))
    with pytest.raises(TimeoutError):
        runtime.run(lambda: ran.append("late"), timeout=0.05)
    release.set()
    runtime.run(lambda: None)  # 앞 작업이 다 끝날 때까지 기다림
    assert ran == []
