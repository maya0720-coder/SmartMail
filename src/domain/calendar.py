"""업무 달력 규칙. 주간보고 주기는 목요일 00:00 ~ 수요일 23:59(한국 표준시)다(MAIL-04, WIKI-01)."""

from __future__ import annotations

from datetime import datetime, timedelta
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


def local_date_label(moment: datetime) -> str:
    """화면 표시용 'MM/DD(요일)'."""
    local = require_aware(moment).astimezone(KST)
    return f"{local:%m/%d}({'월화수목금토일'[local.weekday()]})"
