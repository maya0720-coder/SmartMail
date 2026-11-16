"""AI-Ground 채팅 화면 가짜 브라우저 페이지·가짜 시계(테스트 공용, 결정기록 0023).

실제 AI-Ground에는 접속하지 않는다.
"""

from __future__ import annotations

from typing import Any

BASE = "https://ai.example.invalid"


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

    @property
    def first(self) -> FakeElement:
        return FakeElement(self._page, self._selector)


class FakeElement:
    """locator().first: 보임 여부·대기·클릭·입력(결정기록 0026의 단계별 동작 확인용)."""

    def __init__(self, page: FakeChatPage, selector: str) -> None:
        self._page = page
        self._selector = selector

    def is_visible(self) -> bool:
        return self._selector in self._page.visible

    def is_enabled(self) -> bool:
        return True

    def wait_for(self, state: str = "visible", timeout: float = 0) -> None:
        self._page.log.append(("wait", self._selector))
        if state == "visible" and not self.is_visible():
            raise TimeoutError(f"Timeout {int(timeout)}ms exceeded")

    def click(self) -> None:
        self._page.click(self._selector)

    def fill(self, value: str) -> None:
        self._page.fill(self._selector, value)

    def press(self, key: str) -> None:
        self._page.press(self._selector, key)

    def input_value(self) -> str:
        return self._page.prompt_value


class FakeChatPage:
    """보내면 답변이 조각(chunk)으로 늘어나는 스트리밍 화면."""

    def __init__(
        self,
        clock: FakeClock,
        chunks: list[str],
        endless: bool = False,
        visible: tuple[str, ...] = ("#q",),
        landing_url: str = BASE + "/chat",
    ) -> None:
        self.log: list[tuple[str, ...]] = []
        self.visible = set(visible)  # 보이는 요소 선택자(입력칸 등)
        self.prompt_value = ""
        self.enter_sends = True  # False면 Enter로 보내지지 않는 화면(폼 제출만 동작)
        self.form_submit_sends = True  # False면 어떤 방법으로도 보내지지 않는 화면
        self.url = "about:blank"
        self._landing_url = landing_url  # 열면 도착하는 주소(SSO로 넘어가는 경우 다른 호스트)
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

    def goto(self, url: str, timeout: int, wait_until: str = "load") -> None:
        self.log.append(("goto", url))
        self.url = self._landing_url

    def click(self, selector: str) -> None:
        self.log.append(("click", selector))
        if selector == "#send":  # 보내기 버튼(테스트 관례 선택자)
            self._send()

    def fill(self, selector: str, value: str) -> None:
        self.log.append(("fill", selector, value))
        self.prompt_value = value

    def _send(self) -> None:
        self.prompt_value = ""
        self.answers.append("")
        self.streaming = True

    def wait_for_timeout(self, ms: float) -> None:
        self.log.append(("wait_ms", str(ms)))

    def evaluate(self, script: str, arg: Any = None) -> Any:
        if "requestSubmit" in script:
            self.log.append(("request_submit", str(arg)))
            if self.form_submit_sends:
                self._send()
            return True
        return ["button submit .send", "button #attach"]

    def press(self, selector: str, key: str) -> None:
        self.log.append(("press", selector, key))
        if self.enter_sends:
            self._send()

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def close(self) -> None:
        self.log.append(("close",))
