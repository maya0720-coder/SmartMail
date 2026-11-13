"""제안 목록 보기 규칙(결정기록 0015, 명세 2-1 2단계): 받은 시각 묶음·확신도 낮음 기준.

순수 계산만 한다(저장·외부 호출 없음).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from domain.calendar import week_range
from domain.clock import KST, require_aware

GROUPS: Final = ("today", "yesterday", "week", "older")
GROUP_LABELS: Final = {"today": "오늘", "yesterday": "어제", "week": "이번 주", "older": "이전"}
LOW_CONFIDENCE: Final = 0.7  # '확신도 낮음' 탭 기준(미만)


def received_group(received_at: datetime | None, now: datetime) -> str:
    """받은 시각 묶음: 오늘·어제·이번 주(목~수, KST)·이전. 받은 시각이 없으면 이전."""
    if received_at is None:
        return "older"
    local = require_aware(received_at).astimezone(KST)
    today = require_aware(now).astimezone(KST).date()
    if local.date() == today:
        return "today"
    if local.date() == today - timedelta(days=1):
        return "yesterday"
    start, end = week_range(now)
    if start <= local < end:
        return "week"
    return "older"


def is_low_confidence(confidence: float | None) -> bool:
    return confidence is not None and confidence < LOW_CONFIDENCE
