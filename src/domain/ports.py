"""도메인이 쓰는 저장소 인터페이스(의존 방향 app → domain ← storage).

도메인 코드는 이 Protocol만 알고, 구현은 `storage` 패키지가 제공한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from domain.models import (
    HistoryCard,
    LedgerEntry,
    MailRef,
    ResolutionStage,
    Task,
    TaskEvent,
    TaskEventType,
    TaskStatus,
)


class Clock(Protocol):
    def now(self) -> datetime:
        """시간대가 있는(aware) 현재 시각."""
        ...


class EventRecorder(Protocol):
    def record(
        self, kind: TaskEventType, *, task_id: int | None = None, detail: str | None = None
    ) -> TaskEvent: ...

    def list_events(
        self,
        *,
        task_id: int | None = None,
        kinds: Sequence[TaskEventType] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[TaskEvent]: ...


class TaskRepository(Protocol):
    def add(self, task: Task) -> Task: ...

    def get(self, task_id: int) -> Task | None: ...

    def update(self, task: Task) -> Task: ...

    def find_by_thread(self, thread_id: str) -> Task | None: ...

    def list_tasks(self, statuses: Sequence[TaskStatus] | None = None) -> list[Task]: ...


class MailRefRepository(Protocol):
    def add(self, ref: MailRef) -> MailRef: ...

    def get(self, ref_id: int) -> MailRef | None: ...

    def find_by_message_id(self, message_id: str) -> MailRef | None: ...

    def link_task(self, ref_id: int, task_id: int) -> None: ...

    def list_for_task(self, task_id: int) -> list[MailRef]: ...

    def set_summary(self, ref_id: int, summary: str | None, state: str | None = None) -> None: ...

    def set_direction(self, ref_id: int, direction: str) -> None: ...

    def set_digest(self, ref_id: int, digest: str | None) -> None: ...

    def add_summary_try(self, ref_id: int) -> int: ...


class SyncStateRepository(Protocol):
    """이름별 동기화 기준 시각(예: 메일 자동 수집 마지막 성공 시각)."""

    def get(self, name: str) -> datetime | None: ...

    def set(self, name: str, synced_at: datetime) -> None: ...


class HistoryRepository(Protocol):
    def add(self, card: HistoryCard) -> HistoryCard: ...

    def get_for_task(self, task_id: int) -> HistoryCard | None: ...

    def list_cards(self) -> list[HistoryCard]: ...


class LedgerRepository(Protocol):
    def upsert(self, entry: LedgerEntry) -> LedgerEntry: ...

    def mark_gk_registered(self, task_id: int) -> None: ...

    def set_resolution(self, task_id: int, stage: ResolutionStage) -> None: ...

    def list_entries(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[LedgerEntry]: ...


class TriageRuleStore(Protocol):
    """'업무 아님' 학습 규칙 저장소(결정기록 0015). 키 해시와 횟수만 다룬다."""

    def counts(self, key_hashes: Sequence[str]) -> dict[str, int]: ...

    def bump(self, kind: str, key_hash: str, delta: int) -> None: ...

    def total(self) -> int: ...

    def clear(self) -> int: ...


class KeyHasher(Protocol):
    """원문 대신 저장할 키 해시를 만든다(키는 security/가 관리)."""

    def digest(self, kind: str, value: str) -> str: ...

    def wipe(self) -> None: ...
