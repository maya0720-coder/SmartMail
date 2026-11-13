"""업무 추출·상태 관리 테스트(TODO-01~07)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from collectors.demo_data import demo_messages
from domain.clock import KST, FixedClock
from domain.mail import MailKey, MailMessage, MailThread, group_threads
from domain.models import MailRef, MailSource, TaskEventType, TaskStatus
from domain.retention_rules import retention_info
from domain.tasks.extract import TaskExtractor, clean_title, parse_due, requester_dept
from domain.tasks.service import TaskEdit, TaskService, TransitionError, due_badge
from integrations.ai.fake import FakeKnowledgeAI
from security.masking import MaskedText
from storage.db import Database
from storage.knowledge_repos import SqliteLedgerRepository
from storage.repositories import (
    SqliteEventRecorder,
    SqliteMailRefRepository,
    SqliteTaskRepository,
)

BASE = datetime(2026, 11, 12, 9, 0, tzinfo=KST)  # 목요일


def _msg(n: int, thread: str, when: datetime, **kw: Any) -> MailMessage:
    values: dict[str, Any] = {
        "key": MailKey(MailSource.OUTLOOK, f"E{n}", f"<t{n}@x.invalid>"),
        "thread_id": thread,
        "subject": "[요청] 전표 오류 확인",
        "sender_name": "재무팀 이예시",
        "received_at": when,
        "folder": "받은 편지함",
        "to_me": True,
        "body": "전표 역분개 오류가 납니다. 11/13까지 확인 부탁드립니다.",
    }
    values.update(kw)
    return MailMessage(**values)


def _service(db: Database, clock: FixedClock, ai: Any = None) -> TaskService:
    return TaskService(
        SqliteTaskRepository(db),
        SqliteMailRefRepository(db),
        SqliteEventRecorder(db, clock),
        SqliteLedgerRepository(db),
        clock,
        TaskExtractor(ai),
        "본인",
    )


def _events(db: Database, clock: FixedClock, kind: TaskEventType) -> int:
    return len(SqliteEventRecorder(db, clock).list_events(kinds=[kind]))


def test_todo01_extracts_fields() -> None:
    candidate = TaskExtractor().extract(MailThread("T", (_msg(1, "T", BASE),)))
    assert candidate is not None
    assert candidate.title == "전표 오류 확인"
    assert candidate.requester_dept == "재무팀"
    assert candidate.module_tag is None  # 구분기 없으면 비움(SAP 모듈 추정 안 함, 결정기록 0014)
    assert candidate.due_at == datetime(2026, 11, 13, 23, 59, 59, tzinfo=KST)
    assert "부탁" in candidate.evidence


def test_non_request_mail_not_extracted() -> None:
    message = _msg(1, "T", BASE, subject="점심 메뉴", body="오늘 점심 같이 해요.", to_me=False)
    assert TaskExtractor().extract(MailThread("T", (message,))) is None


def test_ambiguous_mail_uses_ai_with_masked_summary() -> None:
    fake = FakeKnowledgeAI()

    class Classifier:
        def classify_task(self, summary: MaskedText, task_id: int | None = None) -> Any:
            return fake.classify_task(summary)

    message = _msg(
        1, "T", BASE, subject="자료 공유", body="권한 확인 요청드립니다.", to_me=False, cc_me=True
    )
    assert TaskExtractor(Classifier()).extract(MailThread("T", (message,))) is not None
    assert isinstance(fake.received[0], MaskedText)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11/14까지 부탁", datetime(2026, 11, 14, 23, 59, 59, tzinfo=KST)),
        ("오늘 중 처리", datetime(2026, 11, 12, 23, 59, 59, tzinfo=KST)),
        ("내일까지", datetime(2026, 11, 13, 23, 59, 59, tzinfo=KST)),
        ("금요일까지", datetime(2026, 11, 13, 23, 59, 59, tzinfo=KST)),
        ("다음 주 수요일까지", datetime(2026, 11, 18, 23, 59, 59, tzinfo=KST)),
        ("1/5까지", datetime(2027, 1, 5, 23, 59, 59, tzinfo=KST)),
        ("13/40까지", None),
        ("기한 없음", None),
    ],
)
def test_due_parsing(text: str, expected: datetime | None) -> None:
    assert parse_due(text, BASE) == expected


def test_title_and_dept_helpers() -> None:
    assert clean_title("RE: FW: [요청] PO 출력") == "PO 출력"
    assert requester_dept("홍길동") is None


def _ingest_one(
    service: TaskService, db: Database, clock: FixedClock, messages: list[MailMessage]
) -> Any:
    refs = SqliteMailRefRepository(db)
    for m in messages:
        if refs.find_by_message_id(m.key.message_id) is None:
            refs.add(
                MailRef(m.key.source, m.key.entry_id, m.key.message_id, m.thread_id, m.received_at)
            )
    return service.ingest(group_threads(messages))


def test_todo04_same_thread_three_mails_one_task(db: Database, clock: FixedClock) -> None:
    service = _service(db, clock)
    first = [_msg(1, "T1", BASE)]
    _ingest_one(service, db, clock, first)
    later = first + [
        _msg(2, "T1", BASE + timedelta(hours=1)),
        _msg(3, "T1", BASE + timedelta(hours=2)),
    ]
    result = _ingest_one(service, db, clock, later)
    assert result.suggested == []
    [task] = service.list_tasks()
    assert task.id is not None
    assert len(SqliteMailRefRepository(db).list_for_task(task.id)) == 3


def test_todo02_03_approve_edit_dismiss_events(db: Database, clock: FixedClock) -> None:
    service = _service(db, clock)
    _ingest_one(service, db, clock, [_msg(1, "A", BASE), _msg(2, "B", BASE), _msg(3, "C", BASE)])
    a, b, c = service.list_tasks()
    assert a.status is TaskStatus.SUGGESTED and a.id and b.id and c.id
    service.approve(a.id)
    service.approve(b.id, TaskEdit(title="수정된 제목"))
    service.dismiss(c.id)
    assert _events(db, clock, TaskEventType.APPROVED) == 1
    assert _events(db, clock, TaskEventType.APPROVED_EDITED) == 1
    assert _events(db, clock, TaskEventType.DISMISSED) == 1
    assert service.get(c.id).closed_at is not None


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        ([], TaskStatus.DONE),  # 제안됨 → 완료 금지
        ([TaskStatus.WAITING], TaskStatus.SUGGESTED),
        ([TaskStatus.DISMISSED], TaskStatus.WAITING),
    ],
)
def test_illegal_transitions_rejected(
    db: Database, clock: FixedClock, path: list[TaskStatus], bad: TaskStatus
) -> None:
    service = _service(db, clock)
    _ingest_one(service, db, clock, [_msg(1, "A", BASE)])
    [task] = service.list_tasks()
    assert task.id is not None
    for step in path:
        service.change_status(task.id, step)
    with pytest.raises(TransitionError):
        service.change_status(task.id, bad)


def test_todo05_reply_marks_candidate_not_done(db: Database, clock: FixedClock) -> None:
    service = _service(db, clock)
    _ingest_one(service, db, clock, [_msg(1, "R", BASE)])
    [task] = service.list_tasks()
    assert task.id is not None
    service.approve(task.id)
    reply = _msg(
        2, "R", BASE + timedelta(hours=3), sent_by_me=True, to_me=False, folder="보낸 편지함"
    )
    result = _ingest_one(service, db, clock, [_msg(1, "R", BASE), reply])
    assert result.reply_detected == 1
    assert service.get(task.id).status is TaskStatus.DONE_CANDIDATE
    assert _events(db, clock, TaskEventType.COMPLETED) == 0


def test_done_writes_ledger_with_due_compliance(db: Database, clock: FixedClock) -> None:
    service = _service(db, clock)
    task = service.add_manual("수동 업무", TaskEdit(due_at=clock.now() + timedelta(hours=1)))
    assert task.id is not None and task.status is TaskStatus.WAITING  # TODO-06
    service.change_status(task.id, TaskStatus.DONE)
    [entry] = SqliteLedgerRepository(db).list_entries()
    assert entry.has_due and entry.due_met is True
    assert _events(db, clock, TaskEventType.COMPLETED) == 1


def test_todo07_due_badges(clock: FixedClock, db: Database) -> None:
    service = _service(db, clock)
    soon = service.add_manual("임박", TaskEdit(due_at=clock.now() + timedelta(hours=5)))
    late = service.add_manual("지연", TaskEdit(due_at=clock.now() - timedelta(hours=1)))
    none = service.add_manual("여유", TaskEdit(due_at=clock.now() + timedelta(days=5)))
    now = clock.now()
    assert [due_badge(t, now) for t in (soon, late, none)] == ["soon", "overdue", None]


def test_invalid_edit_rejected(db: Database, clock: FixedClock) -> None:
    service = _service(db, clock)
    task = service.add_manual("업무")
    assert task.id is not None
    with pytest.raises(ValueError):
        service.edit(task.id, TaskEdit(module_tag="XX"))
    with pytest.raises(ValueError):
        service.add_manual("  ")


def test_ret02_alert_exactly_two_days_before(db: Database, clock: FixedClock) -> None:
    service = _service(db, clock)
    task = service.add_manual("완료 업무")
    assert task.id is not None
    done = service.change_status(task.id, TaskStatus.DONE)
    retention = timedelta(days=7)
    info = retention_info(done, clock.now() + timedelta(days=5), retention)
    assert info is not None and info.alert and info.days_left == 2
    info = retention_info(done, clock.now() + timedelta(days=5) - timedelta(seconds=1), retention)
    assert info is not None and not info.alert
    service.mark_gk_registered(task.id, "https://gk.example.invalid/k/1")
    info = retention_info(service.get(task.id), clock.now() + timedelta(days=6), retention)
    assert info is not None and not info.alert


def test_demo_set_extraction_accuracy() -> None:
    from collectors.demo_data import DEMO_TASK_THREADS
    from domain.mail_rules import ExclusionRules

    rules = ExclusionRules(subject_keywords=("[공지]",), sender_patterns=("noreply",))
    threads = rules.filter_threads(group_threads(demo_messages(BASE - timedelta(days=6))))
    found = {t.thread_id for t in threads if TaskExtractor(None).extract(t)}
    assert found <= DEMO_TASK_THREADS
    assert len(found) >= len(DEMO_TASK_THREADS) - 1
