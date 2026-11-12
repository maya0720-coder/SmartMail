"""메일 수집·원본 조회 서비스(MAIL-04·05·08~10).

수집 결과는 참조 ID(`mail_ref`, 요약 없음)와 '수신' 이벤트로만 기록한다. 본문은 반환값으로만 넘긴다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from domain.calendar import week_range
from domain.clock import KST
from domain.errors import UserInputError
from domain.mail import MailCollector, MailKey, MailMessage, MailThread
from domain.mail_rules import ExclusionRules
from domain.models import MailRef, TaskEventType
from domain.ports import Clock, EventRecorder, MailRefRepository, SyncStateRepository

MISSING_ORIGINAL = "원문 없음"

# 자동 수집 커서 이름(sync_state.name)
MAIL_SYNC_NAME: Final = "mail"
# 처음 실행 시 자동 수집 기간 기본값(일). 기획서 MAIL-04 개정(결정기록 0009)
DEFAULT_INITIAL_SYNC_DAYS: Final = 30
# 증분 수집 시 마지막 수집 시각보다 이만큼 앞에서 다시 읽는다.
# 아웃룩 캐시 모드 동기화 지연으로 늦게 도착한 메일을 놓치지 않기 위함.
# 겹치는 구간의 중복은 Message-ID로 걸러진다.
SYNC_OVERLAP: Final = timedelta(hours=1)
# 수동 기간 지정 수집의 최대 길이(일). 한 번에 너무 많은 메일을 읽어 아웃룩이 멈추지 않게 한다.
MAX_MANUAL_RANGE_DAYS: Final = 366


@dataclass(frozen=True, slots=True)
class CollectionResult:
    threads: list[MailThread]
    new_messages: int
    excluded_messages: int
    since: datetime
    until: datetime
    initial: bool = False  # 처음 실행 자동 수집(최근 N일)이었는지


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
        *,
        sync_state: SyncStateRepository | None = None,
        initial_sync_days: int = DEFAULT_INITIAL_SYNC_DAYS,
    ) -> None:
        if initial_sync_days < 1:
            raise ValueError("처음 수집 기간은 1일 이상이어야 합니다")
        self.collector = collector
        self._refs = refs
        self._events = events
        self.rules = rules
        self._clock = clock
        self._sync_state = sync_state
        self.initial_sync_days = initial_sync_days
        # 자동 수집(주기 작업)과 화면 버튼이 겹쳐도 한 번에 하나만 수집한다.
        self._collect_lock = threading.Lock()

    @property
    def last_synced_at(self) -> datetime | None:
        return None if self._sync_state is None else self._sync_state.get(MAIL_SYNC_NAME)

    def sync(self) -> CollectionResult:
        """자동 수집(MAIL-04). 처음이면 최근 N일, 이후에는 마지막 수집 시각부터 지금까지 읽는다.

        수집에 성공했을 때만 기준 시각을 옮긴다(실패하면 다음 수집에서 같은 구간을 다시 읽음).
        """
        if self._sync_state is None:
            raise RuntimeError("동기화 저장소가 없습니다")
        now = self._clock.now()
        last = self._sync_state.get(MAIL_SYNC_NAME)
        initial = last is None
        if last is None:
            since = now - timedelta(days=self.initial_sync_days)
        else:
            since = min(last, now) - SYNC_OVERLAP
        # 오래 쓰지 않았어도 한 번에 읽는 범위는 수동 수집 상한을 넘지 않는다.
        since = max(since, now - timedelta(days=MAX_MANUAL_RANGE_DAYS))
        result = self.collect(since, now)
        self._sync_state.set(MAIL_SYNC_NAME, now)
        return CollectionResult(
            result.threads,
            result.new_messages,
            result.excluded_messages,
            result.since,
            result.until,
            initial=initial,
        )

    def collect_range(self, first_day: date, last_day: date) -> CollectionResult:
        """수동 기간 지정 수집(MAIL-04). 시작일 00:00 ~ 종료일 다음날 00:00(KST).

        자동 수집 기준 시각(커서)은 옮기지 않는다.
        """
        today = self._clock.now().astimezone(KST).date()
        if first_day > last_day:
            raise UserInputError("시작일이 종료일보다 늦습니다")
        if last_day > today:
            raise UserInputError("종료일은 오늘 이전이어야 합니다")
        if (last_day - first_day).days + 1 > MAX_MANUAL_RANGE_DAYS:
            raise UserInputError(f"수집 기간은 최대 {MAX_MANUAL_RANGE_DAYS}일입니다")
        since = datetime(first_day.year, first_day.month, first_day.day, tzinfo=KST)
        until = datetime(last_day.year, last_day.month, last_day.day, tzinfo=KST) + timedelta(
            days=1
        )
        return self.collect(since, until)

    def collect(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> CollectionResult:
        """기간(기본: 이번 주 목~수) 메일을 읽어 제외 규칙을 적용하고 참조만 기록한다."""
        if since is None or until is None:
            since, until = week_range(self._clock.now())
        if since >= until:
            raise UserInputError("수집 기간이 올바르지 않습니다")
        with self._collect_lock:
            return self._collect_locked(since, until)

    def _collect_locked(self, since: datetime, until: datetime) -> CollectionResult:
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
