"""주간보고 마감 알림(U6, WIKI-03, 결정기록 0037).

- 설정 `SMARTMAIL_WEEKLY_REMIND_AT`(예: `WED 15:00`, 비우면 끔)의 요일·시각이
  이번 주(목~수) 마감이다.
- 마감일이 주말·공휴일이면 직전 근무일로 앞당긴다(공휴일 목록은 `config/rules/holidays.toml`).
- 마감 시각이 지났는데 이번 주 게시 기록(게시 이력·게시 캐시)이 없으면 배너를 보인다.
- '오늘 숨기기'는 서버 메모리에만 남긴다(브라우저 저장소·DB 사용 안 함).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from domain.calendar import WEEK_START_WEEKDAY, week_start
from domain.clock import KST
from domain.ports import Clock

DAY_NAMES: Final = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
REMIND_PATTERN: Final = re.compile(r"^(MON|TUE|WED|THU|FRI|SAT|SUN) ([01]\d|2[0-3]):([0-5]\d)$")
MAX_HIDDEN_DAYS: Final = 14


@dataclass(frozen=True, slots=True)
class RemindRule:
    weekday: int  # 월=0 … 일=6
    hour: int
    minute: int


def parse_remind(value: str) -> RemindRule | None:
    """'WED 15:00' → 규칙. 빈 값이면 None(끔). 형식이 틀리면 ValueError."""
    text = value.strip().upper()
    if not text:
        return None
    match = REMIND_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError("주간보고 마감 알림은 'WED 15:00' 형식이어야 합니다")
    return RemindRule(DAY_NAMES.index(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_business_day(day: date, holidays: frozenset[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


def due_at(rule: RemindRule, moment: datetime, holidays: frozenset[date]) -> datetime:
    """이번 주(목~수) 마감 시각. 주말·공휴일이면 직전 근무일로 앞당긴다."""
    start = week_start(moment)
    day = (start + timedelta(days=(rule.weekday - WEEK_START_WEEKDAY) % 7)).date()
    for _ in range(14):  # 연휴가 길어도 2주 안에서 멈춘다
        if is_business_day(day, holidays):
            break
        day -= timedelta(days=1)
    return datetime(day.year, day.month, day.day, rule.hour, rule.minute, tzinfo=KST)


class WeeklyReminder:
    def __init__(
        self,
        rule: RemindRule | None,
        clock: Clock,
        published: Callable[[datetime], bool],
        holidays: frozenset[date] = frozenset(),
    ) -> None:
        self._rule = rule
        self._clock = clock
        self._published = published
        self._holidays = holidays
        self._hidden: set[date] = set()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._rule is not None

    def state(self) -> dict[str, object]:
        """배너 상태: show(보일지), due_at(이번 주 마감), reason(안 보이는 이유 코드)."""
        if self._rule is None:
            return {"show": False, "due_at": None, "reason": "off"}
        now = self._clock.now().astimezone(KST)
        due = due_at(self._rule, now, self._holidays)
        info: dict[str, object] = {"due_at": due.isoformat()}
        if now < due:
            return {**info, "show": False, "reason": "before_due"}
        if self._published(week_start(now)):
            return {**info, "show": False, "reason": "published"}
        with self._lock:
            if now.date() in self._hidden:
                return {**info, "show": False, "reason": "hidden_today"}
        return {**info, "show": True, "reason": None}

    def hide_today(self) -> None:
        today = self._clock.now().astimezone(KST).date()
        with self._lock:
            self._hidden = {d for d in self._hidden if (today - d).days < MAX_HIDDEN_DAYS}
            self._hidden.add(today)
