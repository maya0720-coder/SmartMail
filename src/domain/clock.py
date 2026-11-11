"""시각 기준. 업무 규칙(주간 목~수 등)은 한국 표준시로 계산한다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Final

# 한국 표준시(일광절약시간 없음). Windows에서 tzdata 의존 없이 쓰려고 고정 오프셋으로 둔다.
KST: Final = timezone(timedelta(hours=9), "KST")


class SystemClock:
    """실제 시스템 시각(UTC aware)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """테스트·데모용 고정 시각. `advance`로 시간을 옮긴다."""

    def __init__(self, current: datetime) -> None:
        if current.tzinfo is None:
            raise ValueError("시간대가 있는 시각이 필요합니다")
        self._current = current

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        self._current += delta


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("시간대가 있는 시각이 필요합니다")
    return value
