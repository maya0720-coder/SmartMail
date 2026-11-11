"""업무 상태 관리(TODO-02~07). 상태 전이를 검증하고, 전이마다 이벤트를 기록한다.

도메인 코드는 저장소 인터페이스(`domain.ports`)만 쓴다. 수집기·DB를 직접 부르지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from domain.errors import UserInputError
from domain.mail import MailThread
from domain.models import LedgerEntry, Task, TaskEventType, TaskSource, TaskStatus
from domain.ports import (
    Clock,
    EventRecorder,
    LedgerRepository,
    MailRefRepository,
    TaskRepository,
)
from domain.tasks.extract import TaskCandidate, TaskExtractor
from domain.tasks.modules import MODULE_TAGS
from security.masking import mask

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

    def validate(self) -> None:
        if self.title is not None and not 1 <= len(self.title.strip()) <= 200:
            raise UserInputError("제목은 1~200자여야 합니다")
        if self.requester_dept is not None and len(self.requester_dept) > 100:
            raise UserInputError("요청 부서는 100자 이하여야 합니다")
        if self.module_tag is not None and self.module_tag not in MODULE_TAGS:
            raise UserInputError("허용되지 않은 모듈 태그입니다")
        if self.due_at is not None and self.due_at.tzinfo is None:
            raise UserInputError("기한에는 시간대가 필요합니다")

    def apply(self, task: Task) -> Task:
        self.validate()
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
    ) -> None:
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
        updated = edit.apply(task) if edit else task
        changed = updated != task
        saved = self._save(replace(updated, status=S.WAITING))
        kind = TaskEventType.APPROVED_EDITED if changed else TaskEventType.APPROVED
        self._events.record(kind, task_id=task_id)
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
        saved = self._save(edit.apply(task))
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
