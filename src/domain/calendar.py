"""업무 달력 규칙. 주간보고 주기는 목요일 00:00 ~ 수요일 23:59(한국 표준시)다(MAIL-04, WIKI-01)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Final

from domain.clock import KST, require_aware

WEEK_START_WEEKDAY: Final = 3  # 월=0 … 목=3


def week_start(moment: datetime) -> datetime:
    """moment가 속한 주(목~수)의 시작 시각(목 00:00 KST)."""
    local = require_aware(moment).astimezone(KST)
    days_back = (local.weekday() - WEEK_START_WEEKDAY) % 7
    start = local - timedelta(days=days_back)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def week_range(moment: datetime) -> tuple[datetime, datetime]:
    """[시작, 끝) 반열린 구간. 끝은 다음 주 목 00:00(=수 23:59:59.999… 포함)."""
    start = week_start(moment)
    return start, start + timedelta(days=7)


MORNING_HOUR: Final = 8


def next_business_morning(moment: datetime, hour: int = MORNING_HOUR) -> datetime:
    """다음 근무일 아침(KST). 월~목 → 다음 날, 금·토·일 → 다음 월요일(결정기록 0015).

    공휴일은 고려하지 않는다(사내 달력 연동 전, 확인 필요).
    """
    local = require_aware(moment).astimezone(KST)
    days = {4: 3, 5: 2, 6: 1}.get(local.weekday(), 1)  # 금 +3, 토 +2, 일 +1, 그 외 +1
    target = local + timedelta(days=days)
    return target.replace(hour=hour, minute=0, second=0, microsecond=0)


def local_date_label(moment: datetime) -> str:
    """화면 표시용 'MM/DD(요일)'."""
    local = require_aware(moment).astimezone(KST)
    return f"{local:%m/%d}({'월화수목금토일'[local.weekday()]})"


def is_business_day(day: date, holidays: frozenset[date] = frozenset()) -> bool:
    """근무일: 월~금이고 공휴일 목록(config/rules/holidays.toml)에 없는 날."""
    return day.weekday() < 5 and day not in holidays


def business_days_after(
    start: datetime, end: datetime, holidays: frozenset[date] = frozenset()
) -> int:
    """start 날짜 다음 날부터 end 날짜까지 근무일 수(KST 날짜 기준). end가 앞이면 0."""
    first = require_aware(start).astimezone(KST).date()
    last = require_aware(end).astimezone(KST).date()
    count = 0
    day = first + timedelta(days=1)
    while day <= last and count < 366:
        if is_business_day(day, holidays):
            count += 1
        day += timedelta(days=1)
    return count
