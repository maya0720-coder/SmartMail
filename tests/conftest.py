"""공통 테스트 fixture. 모든 데이터는 합성 값이다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from domain.clock import FixedClock
from storage.db import Database

# 기준 시각: 2026-11-12(목) 09:00 KST = 00:00 UTC
BASE_TIME = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(BASE_TIME)


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database.in_memory()
    yield database
    database.close()
