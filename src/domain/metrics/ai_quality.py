"""AI 요약·해결 방안 품질 지표(HIST-06, 결정기록 0021).

- 해결 방안 '도움 됨' 비율: 사용자가 누른 평가(FEEDBACK 이벤트)만 센다. 내용·사람 정보 없음.
- AI 호출 성공률: AI 호출 이벤트(성공/실패 코드값)만 센다.
표본이 적으면(MIN_SAMPLE 미만) 참고값으로 표시한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from domain.models import TaskEventType
from domain.ports import EventRecorder

WINDOW_DAYS: Final = 30
MIN_SAMPLE: Final = 5


@dataclass(frozen=True, slots=True)
class AiQuality:
    helpful: int
    not_helpful: int
    ai_success: int
    ai_failure: int
    window_days: int = WINDOW_DAYS

    @property
    def helpful_rate(self) -> float | None:
        total = self.helpful + self.not_helpful
        return None if total == 0 else round(self.helpful / total, 3)

    @property
    def ai_success_rate(self) -> float | None:
        total = self.ai_success + self.ai_failure
        return None if total == 0 else round(self.ai_success / total, 3)

    @property
    def reference_only(self) -> bool:
        return self.helpful + self.not_helpful < MIN_SAMPLE


def ai_quality(events: EventRecorder, now: datetime, days: int = WINDOW_DAYS) -> AiQuality:
    since = now - timedelta(days=days)
    feedback = events.list_events(kinds=[TaskEventType.FEEDBACK], since=since)
    calls = events.list_events(kinds=[TaskEventType.AI_CALLED], since=since)
    return AiQuality(
        helpful=sum(e.detail == "helpful" for e in feedback),
        not_helpful=sum(e.detail == "not_helpful" for e in feedback),
        ai_success=sum(e.detail == "success" for e in calls),
        ai_failure=sum(e.detail == "failure" for e in calls),
        window_days=days,
    )
