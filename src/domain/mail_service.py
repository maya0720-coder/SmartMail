"""메일 수집·원본 조회 서비스(MAIL-04·05·08~10).

수집 결과는 참조 ID(`mail_ref`, 요약 없음)와 '수신' 이벤트로만 기록한다. 본문은 반환값으로만 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.calendar import week_range
from domain.errors import UserInputError
from domain.mail import MailCollector, MailKey, MailMessage, MailThread
from domain.mail_rules import ExclusionRules
from domain.models import MailRef, TaskEventType
from domain.ports import Clock, EventRecorder, MailRefRepository

MISSING_ORIGINAL = "원문 없음"


@dataclass(frozen=True, slots=True)
class CollectionResult:
    threads: list[MailThread]
    new_messages: int
    excluded_messages: int
    since: datetime
    until: datetime


@dataclass(frozen=True, slots=True)
class MailView:
    ref: MailRef
    message: MailMessage | None

    @property
    def missing(self) -> bool:
        return self.message is None


def key_of(ref: MailRef) -> MailKey:
    return MailKey(ref.source, ref.entry_id, ref.message_id)


class MailService:
    def __init__(
        self,
        collector: MailCollector,
        refs: MailRefRepository,
        events: EventRecorder,
        rules: ExclusionRules,
        clock: Clock,
    ) -> None:
        self.collector = collector
        self._refs = refs
        self._events = events
        self.rules = rules
        self._clock = clock

    def collect(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> CollectionResult:
        """기간(기본: 이번 주 목~수) 메일을 읽어 제외 규칙을 적용하고 참조만 기록한다."""
        if since is None or until is None:
            since, until = week_range(self._clock.now())
        if since >= until:
            raise UserInputError("수집 기간이 올바르지 않습니다")
        raw = self.collector.list_threads(since, until)
        total = sum(len(t.messages) for t in raw)
        threads = self.rules.filter_threads(raw)
        kept = sum(len(t.messages) for t in threads)
        new = 0
        for thread in threads:
            for message in thread.messages:
                if self._refs.find_by_message_id(message.key.message_id) is not None:
                    continue
                self._refs.add(
                    MailRef(
                        source=message.key.source,
                        entry_id=message.key.entry_id,
                        message_id=message.key.message_id,
                        thread_id=message.thread_id,
                        received_at=message.received_at,
                    )
                )
                if not message.sent_by_me:
                    self._events.record(TaskEventType.RECEIVED)
                new += 1
        return CollectionResult(threads, new, total - kept, since, until)

    def view(self, ref_id: int) -> MailView | None:
        """원본을 매번 다시 읽는다. 찾을 수 없으면 message=None('원문 없음', MAIL-09)."""
        ref = self._refs.get(ref_id)
        if ref is None:
            return None
        if ref.source != self.collector.source:
            return MailView(ref, None)
        return MailView(ref, self.collector.get_message(key_of(ref)))

    def refs_for_task(self, task_id: int) -> list[MailRef]:
        return self._refs.list_for_task(task_id)

    def open_in_client(self, ref_id: int) -> bool:
        ref = self._refs.get(ref_id)
        if ref is None or not self.collector.can_open_in_client:
            return False
        return self.collector.open_in_client(key_of(ref))
