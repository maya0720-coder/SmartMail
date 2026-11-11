"""수집기 공통 요소. 인터페이스·모델은 `domain.mail`에 있고 여기서 다시 내보낸다."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from domain.mail import (
    CollectorUnavailableError,
    MailCollector,
    MailKey,
    MailMessage,
    MailThread,
    group_threads,
)

__all__ = [
    "CollectorUnavailableError",
    "MailCollector",
    "MailKey",
    "MailMessage",
    "MailThread",
    "SingleThreadRunner",
    "group_threads",
]

T = TypeVar("T")

MAX_BODY_CHARS = 50_000


class SingleThreadRunner:
    """COM 개체처럼 만든 스레드에서만 써야 하는 호출을 전용 스레드 하나에서 실행한다."""

    def __init__(self, name: str, initializer: Callable[[], None] | None = None) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=name, initializer=initializer
        )

    def run(self, func: Callable[[], T], timeout: float = 120.0) -> T:
        return self._executor.submit(func).result(timeout)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
