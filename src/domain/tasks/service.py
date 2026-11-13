"""업무 상태 관리(TODO-02~07). 상태 전이를 검증하고, 전이마다 이벤트를 기록한다.

도메인 코드는 저장소 인터페이스(`domain.ports`)만 쓴다. 수집기·DB를 직접 부르지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from domain.calendar import next_business_morning
from domain.errors import UserInputError
from domain.mail import MailThread
from domain.models import (
    MAX_REASONS,
    WEEKLY_SECTIONS,
    LedgerEntry,
    Task,
    TaskEventType,
    TaskSource,
    TaskStatus,
)
from domain.ports import (
    Clock,
    EventRecorder,
    LedgerRepository,
    MailRefRepository,
    TaskRepository,
)
from domain.tasks.categories import DEFAULT_CATEGORIES, CategoryCatalog
from domain.tasks.extract import TaskCandidate, TaskExtractor
from domain.tasks.triage_rules import RuleKey, TriageLearning
from security.masking import mask

logger = logging.getLogger(__name__)

S = TaskStatus
ALLOWED_TRANSITIONS: Final[dict[TaskStatus, frozenset[TaskStatus]]] = {
    S.SUGGESTED: frozenset({S.WAITING, S.DISMISSED}),
    S.WAITING: frozenset({S.IN_PROGRESS, S.ON_HOLD, S.DONE_CANDIDATE, S.DONE}),
    S.IN_PROGRESS: frozenset({S.WAITING, S.ON_HOLD, S.DONE_CANDIDATE, S.DONE}),
    S.DONE_CANDIDATE: frozenset({S.DONE, S.IN_PROGRESS, S.WAITING}),
    S.ON_HOLD: frozenset({S.WAITING, S.IN_PROGRESS}),
    S.DONE: frozenset({S.IN_PROGRESS}),  # 완료 취소(재작업)
    S.DISMISSED: frozenset(),
}

DUE_SOON: Final = timedelta(days=1)
MAX_EVIDENCE_CACHE: Final = 500


# 결정 종류 → 결정 직후 상태(결정 취소 때 확인)
TRIAGE_AFTER: Final = {
    "approved": TaskStatus.WAITING,
    "snoozed": TaskStatus.SUGGESTED,
    "dismissed": TaskStatus.DISMISSED,
}


class TransitionError(UserInputError):
    """허용되지 않은 상태 전이."""


class TaskNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class TaskEdit:
    """사용자 수정 항목. None이면 바꾸지 않는다."""

    title: str | None = None
    requester_dept: str | None = None
    due_at: datetime | None = None
    module_tag: str | None = None
    clear_due: bool = False
    weekly_section: str | None = None  # 주간보고 위치(사용자 선택, 결정기록 0015)

    def validate(self, categories: Sequence[str] = DEFAULT_CATEGORIES) -> None:
        if self.title is not None and not 1 <= len(self.title.strip()) <= 200:
            raise UserInputError("제목은 1~200자여야 합니다")
        if self.requester_dept is not None and len(self.requester_dept) > 100:
            raise UserInputError("요청 부서는 100자 이하여야 합니다")
        if self.module_tag is not None and self.module_tag not in categories:
            raise UserInputError("설정에 없는 구분입니다")
        if self.due_at is not None and self.due_at.tzinfo is None:
            raise UserInputError("기한에는 시간대가 필요합니다")
        if self.weekly_section is not None and self.weekly_section not in WEEKLY_SECTIONS:
            raise UserInputError("주간보고 위치가 올바르지 않습니다")

    def apply(self, task: Task, categories: Sequence[str] = DEFAULT_CATEGORIES) -> Task:
        self.validate(categories)
        return replace(
            task,
            title=mask(self.title.strip()).value if self.title is not None else task.title,
            requester_dept=(
                mask(self.requester_dept).value
                if self.requester_dept is not None
                else task.requester_dept
            ),
            due_at=None if self.clear_due else (self.due_at or task.due_at),
            module_tag=self.module_tag if self.module_tag is not None else task.module_tag,
            weekly_section=(
                self.weekly_section if self.weekly_section is not None else task.weekly_section
            ),
        )


@dataclass(frozen=True, slots=True)
class IngestResult:
    suggested: list[tuple[Task, TaskCandidate]]
    linked: int
    reply_detected: int


def due_badge(task: Task, now: datetime) -> str | None:
    """기한 임박·지연 표시(TODO-07): overdue / soon / None."""
    if task.due_at is None or task.status in (S.DONE, S.DISMISSED):
        return None
    if now > task.due_at:
        return "overdue"
    if task.due_at - now <= DUE_SOON:
        return "soon"
    return None


class TaskService:
    def __init__(
        self,
        tasks: TaskRepository,
        mail_refs: MailRefRepository,
        events: EventRecorder,
        ledger: LedgerRepository,
        clock: Clock,
        extractor: TaskExtractor,
        assignee: str,
        *,
        catalog: CategoryCatalog | None = None,
        learning: TriageLearning | None = None,
    ) -> None:
        self._catalog = catalog
        self._learning = learning
        self._tasks = tasks
        self._refs = mail_refs
        self._events = events
        self._ledger = ledger
        self._clock = clock
        self._extractor = extractor
        self._assignee = assignee
        # 추출 근거 문장(화면 표시용). 메모리에만 두고 저장하지 않는다(MAIL-08).
        # 키: 업무 ID, 값: (업무 생성 시각, 근거). 생성 시각이 다르면(삭제 후 ID 재사용) 무효.
        self._evidence: dict[int, tuple[datetime, str]] = {}

    def categories(self) -> tuple[str, ...]:
        return self._catalog.categories() if self._catalog is not None else DEFAULT_CATEGORIES

    def title_of(self, subject: str) -> str:
        """메일 제목 → 임시 업무 제목(회신 표시·말머리 제거)."""
        return self._extractor.title_of(subject)

    def set_summary_title(self, task_id: int, title: str) -> None:
        """AI 요청 요약으로 업무 제목을 바꾼다(마스킹, 200자).

        사용자 수정이 아니므로 이벤트는 남기지 않는다.
        """
        task = self.get(task_id)
        cleaned = mask(title.strip()).value[:200]
        if cleaned:
            self._save(replace(task, title=cleaned))

    def recategorize_unknown(self) -> int:
        """구분 목록에 없는 값(예: 예전 SAP 모듈 태그)을 가진 업무를 규칙으로 다시 구분한다.

        메일 원문은 저장하지 않으므로 업무 제목으로 판별한다. 바꾼 건수를 돌려준다.
        """
        if self._catalog is None:
            return 0
        allowed = self.categories()
        changed = 0
        for task in self._tasks.list_tasks(None):
            if task.status is S.DISMISSED or task.module_tag in allowed:
                continue
            self._save(replace(task, module_tag=self._catalog.categorize(task.title, "")))
            changed += 1
        return changed

    # ---- 조회 ----
    def get(self, task_id: int) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError("업무를 찾을 수 없습니다")
        return task

    def list_tasks(self, statuses: list[TaskStatus] | None = None) -> list[Task]:
        return self._tasks.list_tasks(statuses)

    def evidence(self, task_id: int) -> str | None:
        cached = self._evidence.get(task_id)
        task = self._tasks.get(task_id)
        if cached is None or task is None or task.created_at != cached[0]:
            self._evidence.pop(task_id, None)
            return None
        return cached[1]

    def clear_cache(self) -> None:
        """전체 삭제·보관기간 삭제 뒤 메모리의 근거 문장도 지운다."""
        self._evidence.clear()

    # ---- 메일 → 업무 ----
    def _link_thread(self, thread: MailThread, task_id: int) -> None:
        for message in thread.messages:
            ref = self._refs.find_by_message_id(message.key.message_id)
            if ref is not None and ref.id is not None and ref.task_id is None:
                self._refs.link_task(ref.id, task_id)

    def ingest(self, threads: list[MailThread]) -> IngestResult:
        """수집한 스레드로 업무를 제안하고, 기존 업무의 후속 메일·회신을 연결한다."""
        suggested: list[tuple[Task, TaskCandidate]] = []
        linked = replied = 0
        for thread in threads:
            existing = self._tasks.find_by_thread(thread.thread_id)
            if existing is not None and existing.id is not None:
                self._link_thread(thread, existing.id)  # TODO-04 중복 생성 금지
                linked += 1
                if self._detect_reply(existing, thread):
                    replied += 1
                continue
            candidate = self._extractor.extract(thread)
            if candidate is None:
                continue
            candidate = self._apply_learning(thread, candidate)
            task = self._tasks.add(
                Task(
                    title=mask(candidate.title).value[:200],
                    status=S.SUGGESTED,
                    source=TaskSource.MAIL,
                    created_at=self._clock.now(),
                    requester_dept=(
                        mask(candidate.requester_dept).value[:100]
                        if candidate.requester_dept
                        else None
                    ),
                    requested_at=candidate.requested_at,
                    due_at=candidate.due_at,
                    module_tag=candidate.module_tag,
                    thread_id=thread.thread_id,
                    confidence=round(candidate.confidence, 3),
                    reasons=candidate.reasons,
                )
            )
            assert task.id is not None  # noqa: S101
            self._link_thread(thread, task.id)
            self._events.record(TaskEventType.SUGGESTED, task_id=task.id)
            self._evidence[task.id] = (task.created_at, candidate.evidence)
            while len(self._evidence) > MAX_EVIDENCE_CACHE:
                self._evidence.pop(next(iter(self._evidence)))
            suggested.append((task, candidate))
        return IngestResult(suggested, linked, replied)

    def _apply_learning(self, thread: MailThread, candidate: TaskCandidate) -> TaskCandidate:
        """'업무 아님' 학습 감점(확신도만 낮춤, 자동 제외 없음)."""
        received = [m for m in thread.messages if not m.sent_by_me]
        if self._learning is None or not received:
            return candidate
        first = received[0]
        try:
            keys = self._learning.keys_for(first.sender_address, first.subject)
            confidence, penalized = self._learning.adjust(candidate.confidence, keys)
        except Exception as exc:  # 학습(보조 기능) 오류가 메일 반영을 멈추지 않게 한다
            logger.warning("업무 아님 학습 감점 생략: %s", type(exc).__name__)
            return candidate
        if not penalized:
            return candidate
        return replace(
            candidate,
            confidence=confidence,
            reasons=(*candidate.reasons[: MAX_REASONS - 1], "learned_penalty"),
        )

    def learn_dismissal(self, sender_address: str, subject: str) -> tuple[RuleKey, ...]:
        """업무 아님으로 고른 메일의 키 해시를 1 올린다. 되돌리기용 키를 돌려준다(원문은 버림)."""
        if self._learning is None:
            return ()
        try:
            keys = self._learning.keys_for(sender_address, subject)
            self._learning.record(keys, +1)
        except Exception as exc:  # 결정은 이미 저장됐다. 학습만 생략하고 결정·되돌리기는 유지
            logger.warning("업무 아님 학습 기록 생략: %s", type(exc).__name__)
            return ()
        return keys

    def unlearn(self, keys: tuple[RuleKey, ...]) -> None:
        if self._learning is None:
            return
        try:
            self._learning.record(keys, -1)
        except Exception as exc:
            logger.warning("업무 아님 학습 취소 생략: %s", type(exc).__name__)

    def learned_rule_count(self) -> int:
        return self._learning.total() if self._learning is not None else 0

    def reset_learning(self) -> int:
        return self._learning.reset() if self._learning is not None else 0

    def _detect_reply(self, task: Task, thread: MailThread) -> bool:
        """내 회신이 마지막 받은 메일 뒤에 있으면 '완료 후보'로 표시한다(TODO-05, 확정은 사용자)."""
        if task.status not in (S.WAITING, S.IN_PROGRESS):
            return False
        mine = [m.received_at for m in thread.messages if m.sent_by_me]
        theirs = [m.received_at for m in thread.messages if not m.sent_by_me]
        if not mine or (theirs and max(mine) < max(theirs)):
            return False
        assert task.id is not None  # noqa: S101
        self._save(replace(task, status=S.DONE_CANDIDATE))
        self._events.record(TaskEventType.REPLIED, task_id=task.id)
        return True

    # ---- 사용자 조작 ----
    def approve(self, task_id: int, edit: TaskEdit | None = None) -> Task:
        task = self.get(task_id)
        self._check(task, S.WAITING)
        updated = edit.apply(task, self.categories()) if edit else task
        changed = updated != task
        saved = self._save(replace(updated, status=S.WAITING, snooze_until=None))
        kind = TaskEventType.APPROVED_EDITED if changed else TaskEventType.APPROVED
        self._events.record(kind, task_id=task_id)
        return saved

    # ---- 제안 트리아지(결정기록 0015) ----
    def snooze(self, task_id: int) -> Task:
        """'나중에': 제안됨 상태 그대로 다음 근무일 아침까지 제안 목록에서 숨긴다."""
        task = self.get(task_id)
        if task.status is not S.SUGGESTED:
            raise TransitionError("제안된 업무만 나중에로 미룰 수 있습니다")
        until = next_business_morning(self._clock.now())
        saved = self._save(replace(task, snooze_until=until))
        self._events.record(TaskEventType.SNOOZED, task_id=task_id)
        return saved

    def is_snoozed(self, task: Task) -> bool:
        return task.snooze_until is not None and task.snooze_until > self._clock.now()

    def triage_decisions_since(self, since: datetime) -> int:
        """since 이후 내린 제안 결정 수(할 일로·업무 아님·나중에 − 되돌림). 이벤트에서 센다."""
        kinds = [
            TaskEventType.APPROVED,
            TaskEventType.APPROVED_EDITED,
            TaskEventType.DISMISSED,
            TaskEventType.SNOOZED,
            TaskEventType.TRIAGE_REVERTED,
        ]
        events = self._events.list_events(kinds=kinds, since=since)
        reverted = sum(1 for e in events if e.kind is TaskEventType.TRIAGE_REVERTED)
        return max(0, len(events) - 2 * reverted)

    def revert_triage(self, decision: str, before: Task) -> Task:
        """결정 취소: 결정 직전 값(before)으로 되돌린다. 상태 전이 표와 별개의 전용 경로다.

        결정 뒤 다른 상태로 더 바뀐 업무는 되돌리지 않는다(TransitionError).
        """
        expected = TRIAGE_AFTER.get(decision)
        if expected is None or before.id is None:
            raise TransitionError("되돌릴 수 없는 결정입니다")
        current = self.get(before.id)
        if current.status is not expected:
            raise TransitionError("이미 다른 상태로 바뀌어 되돌릴 수 없습니다")
        saved = self._save(before)
        self._events.record(TaskEventType.TRIAGE_REVERTED, task_id=before.id, detail=decision)
        return saved

    def dismiss(self, task_id: int) -> Task:
        task = self.get(task_id)
        self._check(task, S.DISMISSED)
        saved = self._save(replace(task, status=S.DISMISSED, closed_at=self._clock.now()))
        self._events.record(TaskEventType.DISMISSED, task_id=task_id)
        return saved

    def edit(self, task_id: int, edit: TaskEdit) -> Task:
        task = self.get(task_id)
        if task.status is S.DISMISSED:
            raise TransitionError("무시한 업무는 수정할 수 없습니다")
        saved = self._save(edit.apply(task, self.categories()))
        self._events.record(TaskEventType.EDITED, task_id=task_id)
        return saved

    def change_status(self, task_id: int, new: TaskStatus) -> Task:
        task = self.get(task_id)
        if new is S.DISMISSED:
            return self.dismiss(task_id)
        if task.status is S.SUGGESTED and new is S.WAITING:
            return self.approve(task_id)
        self._check(task, new)
        now = self._clock.now()
        if new is S.DONE:
            saved = self._save(replace(task, status=S.DONE, closed_at=now))
            self._events.record(TaskEventType.COMPLETED, task_id=task_id)
            self._ledger.upsert(
                LedgerEntry(
                    task_id=task_id,
                    completed_at=now,
                    assignee=self._assignee,
                    has_due=task.due_at is not None,
                    due_met=None if task.due_at is None else now <= task.due_at,
                    gk_registered=task.gk_registered,
                )
            )
            return saved
        saved = self._save(replace(task, status=new, closed_at=None))
        self._events.record(TaskEventType.STATUS_CHANGED, task_id=task_id, detail=new.value)
        return saved

    def add_manual(self, title: str, edit: TaskEdit | None = None) -> Task:
        """수동 업무 추가(TODO-06). 승인 절차 없이 '대기'로 만든다."""
        base = Task(
            title=title,
            status=S.WAITING,
            source=TaskSource.MANUAL,
            created_at=self._clock.now(),
        )
        task = (edit or TaskEdit()).apply(replace(base, title=title))
        TaskEdit(title=task.title).validate()
        return self._tasks.add(task)

    def mark_gk_registered(self, task_id: int, link: str) -> Task:
        task = self.get(task_id)
        saved = self._save(replace(task, gk_link=link, gk_registered_at=self._clock.now()))
        self._events.record(TaskEventType.GK_REGISTERED, task_id=task_id)
        self._ledger.mark_gk_registered(task_id)
        return saved

    # ---- 내부 ----
    @staticmethod
    def _check(task: Task, new: TaskStatus) -> None:
        if new not in ALLOWED_TRANSITIONS[task.status]:
            raise TransitionError("허용되지 않은 상태 변경입니다")

    def _save(self, task: Task) -> Task:
        return self._tasks.update(task)
