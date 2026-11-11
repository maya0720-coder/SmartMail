"""간단한 메모리 기반 요청 속도 제한(로그인 시도 등)."""

from __future__ import annotations

import threading
from collections import deque
from datetime import timedelta

from domain.ports import Clock


class RateLimiter:
    """키별로 window 안의 시도 횟수를 max_attempts로 제한한다."""

    def __init__(self, clock: Clock, max_attempts: int, window: timedelta) -> None:
        if max_attempts < 1 or window <= timedelta(0):
            raise ValueError("속도 제한 값이 올바르지 않습니다")
        self._clock = clock
        self._max = max_attempts
        self._window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> int | None:
        """허용되면 None을 돌려주고 시도를 기록한다. 초과면 재시도까지 남은 초를 돌려준다."""
        now = self._clock.now().timestamp()
        horizon = now - self._window.total_seconds()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= horizon:
                hits.popleft()
            if len(hits) >= self._max:
                return max(1, int(hits[0] - horizon) + 1)
            hits.append(now)
            return None
