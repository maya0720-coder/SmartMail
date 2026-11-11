"""기간 버킷: 주(목~수), 월, 분기, 반기(1~6월/7~12월), 연(달력). 모두 KST 기준.

표시 개수: 연 3 / 반기 6 / 분기 8 / 월 13 / 주 13(기획서 4.9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from domain.calendar import week_start
from domain.clock import KST, require_aware

Grain = Literal["year", "half", "quarter", "month", "week"]
GRAINS: Final[tuple[Grain, ...]] = ("year", "half", "quarter", "month", "week")
DISPLAY_COUNT: Final[dict[Grain, int]] = {
    "year": 3,
    "half": 6,
    "quarter": 8,
    "month": 13,
    "week": 13,
}


@dataclass(frozen=True, slots=True)
class Bucket:
    start: datetime
    end: datetime  # 반열린 구간 [start, end)
    label: str


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=KST)


def _add_months(start: datetime, months: int) -> datetime:
    index = start.year * 12 + (start.month - 1) + months
    return _month_start(index // 12, index % 12 + 1)


_MONTHS_PER: Final[dict[Grain, int]] = {"year": 12, "half": 6, "quarter": 3, "month": 1}


def bucket_start(moment: datetime, grain: Grain) -> datetime:
    local = require_aware(moment).astimezone(KST)
    if grain == "week":
        return week_start(local)
    size = _MONTHS_PER[grain]
    first_month = ((local.month - 1) // size) * size + 1
    return _month_start(local.year, first_month)


def next_start(start: datetime, grain: Grain) -> datetime:
    if grain == "week":
        return start + timedelta(days=7)
    return _add_months(start, _MONTHS_PER[grain])


def _label(start: datetime, grain: Grain) -> str:
    if grain == "year":
        return f"{start.year}"
    if grain == "half":
        return f"{start.year} {'상' if start.month == 1 else '하'}반기"
    if grain == "quarter":
        return f"{start.year} Q{(start.month - 1) // 3 + 1}"
    if grain == "month":
        return f"{start.year}.{start.month:02d}"
    return f"{start:%m/%d}~"


def bucket_of(moment: datetime, grain: Grain) -> Bucket:
    start = bucket_start(moment, grain)
    return Bucket(start, next_start(start, grain), _label(start, grain))


def recent_buckets(now: datetime, grain: Grain, count: int | None = None) -> list[Bucket]:
    """현재(진행 중) 버킷을 마지막으로 하는 최근 버킷 목록(오래된 순)."""
    total = count or DISPLAY_COUNT[grain]
    buckets = [bucket_of(now, grain)]
    while len(buckets) < total:
        previous_end = buckets[0].start
        buckets.insert(0, bucket_of(previous_end - timedelta(microseconds=1), grain))
    return buckets
