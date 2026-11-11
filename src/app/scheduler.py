"""주기 작업 실행기. 보관기간 삭제를 앱 시작 시 1회 + 하루 1회 실행한다(RET-01)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Final

logger = logging.getLogger(__name__)

DAILY_SECONDS: Final = 24 * 60 * 60


class PeriodicJob:
    """동기 함수를 즉시 1회, 이후 interval초마다 실행한다. 실패해도 다음 주기에 다시 시도한다."""

    def __init__(
        self, name: str, func: Callable[[], object], interval_seconds: float = DAILY_SECONDS
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("실행 간격은 0보다 커야 합니다")
        self._name = name
        self._func = func
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self.runs = 0

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self._func)
            except Exception:
                logger.exception("주기 작업 실패: %s", self._name)
            self.runs += 1
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
