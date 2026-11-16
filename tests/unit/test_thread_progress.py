"""스레드 최종 메일 기준 집계·진행 통합 보기·요약 상태 테스트.

TODO-01·TODO-02·MAIL-03·MAIL-08, 결정기록 0028(R2·R3)·0029(R4). 합성 메일·가짜 AI만 쓴다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.container import Container
from domain.clock import FixedClock
from domain.mail import MailKey, MailMessage
from domain.models import MailSource, Task, TaskSource, TaskStatus
from domain.tasks.summarize import MAX_SUMMARY_TRIES, SummaryService
from fixtures.app_factory import app_client, make_container
from integrations.ai.fake import FakeKnowledgeAI
from storage.db import Database
from storage.repositories import SqliteMailRefRepository, SqliteTaskRepository

THREAD = "SYN-THREAD-1"
BODY_MARKER = "본문표식문장-원문-전송금지"
SENDER = "합성팀 홍가상"
ADDRESS = "hong.synthetic@example.invalid"


def _msg(n: int, when: datetime, *, out: bool = False, **kw: Any) -> MailMessage:
    values: dict[str, Any] = {
        "key": MailKey(MailSource.OUTLOOK, f"SYN-{n}", f"<syn-{n}@example.invalid>"),
        "thread_id": THREAD,
        "subject": ("RE: " if n > 1 else "") + "[요청] 합성 전표 오류 확인",
        "sender_name": "본인" if out else SENDER,
        "sender_address": "" if out else ADDRESS,
        "received_at": when,
        "folder": "보낸 편지함" if out else "받은 편지함",
        "sent_by_me": out,
        "to_me": not out,
        "body": f"합성 전표 역분개 오류 확인 부탁드립니다. {BODY_MARKER}",
    }
    values.update(kw)
    return MailMessage(**values)


def _ingest(container: Container, clock: FixedClock, *messages: MailMessage) -> None:
    collector: Any = container.mail.collector
    for message in messages:
        collector.add(message)
    result = container.mail.collect(clock.now() - timedelta(days=3), clock.now())
    container.tasks.ingest(result.threads)


def _thread_task(container: Container) -> Task:
    task = SqliteTaskRepository(container.db).find_by_thread(THREAD)
    assert task is not None and task.id is not None
    return task


def _three(clock: FixedClock) -> tuple[MailMessage, ...]:
    now = clock.now()
    return (
        _msg(1, now - timedelta(hours=5)),
        _msg(2, now - timedelta(hours=3)),
        _msg(3, now - timedelta(hours=1), out=True),
    )


# ---- R2: 스레드 최종 메일 ----
def test_todo01_thread_is_one_row_with_last_mail(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    msgs = _three(clock)
    _ingest(container, clock, *msgs)
    task = _thread_task(container)
    assert task.last_mail_at == msgs[2].received_at and task.last_mail_direction == "out"
    with app_client(container) as client:
        rows = [t for t in client.get("/api/tasks").json() if t["id"] == task.id]
    assert len(rows) == 1
    row = rows[0]
    assert row["mail_count"] == 3 and row["thread_count"] == 3
    assert row["last_mail_direction"] == "out" and row["replied_last"] is True
    assert datetime.fromisoformat(row["last_mail_at"]) == msgs[2].received_at
    # 요청일(최초 요청)은 첫 메일 기준 그대로
    assert datetime.fromisoformat(row["requested_at"]) == msgs[0].received_at


def test_todo01_followup_moves_row_to_top(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    _ingest(container, clock, *_three(clock))
    clock.advance(timedelta(hours=2))
    _ingest(container, clock, _msg(4, clock.now() - timedelta(minutes=5)))
    task = _thread_task(container)
    with app_client(container) as client:
        rows = client.get("/api/tasks").json()
    latest = max(rows, key=lambda t: t["last_mail_at"] or "")
    assert latest["id"] == task.id and latest["last_mail_direction"] == "in"


def test_todo01_legacy_rows_without_new_columns(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    _ingest(container, clock, *_three(clock))
    task = _thread_task(container)
    with container.db.transaction() as conn:  # 이관 전 DB처럼 새 열을 비운다
        conn.execute("UPDATE task SET last_mail_at = NULL, last_mail_direction = NULL")
        conn.execute("UPDATE mail_ref SET direction = NULL, summary_state = NULL")
    with app_client(container) as client:
        row = next(t for t in client.get("/api/tasks").json() if t["id"] == task.id)
        detail = client.get(f"/api/tasks/{task.id}").json()
    # 저장값이 없으면 메일 참조(마지막 메일 시각)로 계산하고, 방향을 모르면 비워 둔다
    assert row["last_mail_direction"] is None and row["last_mail_at"] is not None
    assert row["summary_state"] == "pending"
    assert [r["direction"] for r in detail["mail_refs"]] == [None, None, None]
    # 다음 수집 때 방향을 채운다
    container.mail.collect(clock.now() - timedelta(days=3), clock.now())
    refs = SqliteMailRefRepository(container.db).list_for_task(task.id or 0)
    assert [r.direction for r in refs] == ["in", "in", "out"]


# ---- R3: 진행 경과 ----
def test_todo01_detail_progress_and_timeline(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    now = clock.now()
    _ingest(container, clock, *_three(clock), _msg(4, now - timedelta(minutes=30)))
    task = _thread_task(container)
    collector: Any = container.mail.collector
    collector.delete("SYN-2")  # 원본 삭제(MAIL-09)
    with app_client(container) as client:
        detail = client.get(f"/api/tasks/{task.id}").json()
        timeline = client.get(f"/api/tasks/{task.id}/timeline").json()["items"]
    assert [r["direction"] for r in detail["mail_refs"]] == ["in", "in", "out", "in"]
    assert detail["progress"].startswith("첫 요청(")
    assert "후속 2통" in detail["progress"] and "내 회신 1통" in detail["progress"]
    assert "마지막: 받음(" in detail["progress"]
    assert [t["direction"] for t in timeline] == ["in", "in", "out", "in"]
    assert timeline[1]["missing"] is True and timeline[1]["sender_name"] is None
    assert timeline[0]["sender_name"] == SENDER  # 화면 표시만(응답), 저장하지 않음
    stored = container.db.query("SELECT * FROM mail_ref")
    assert all(SENDER not in str(dict(row)) for row in stored)


def test_ai02_progress_summary_payload_has_no_original(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    _ingest(container, clock, *_three(clock))
    task = _thread_task(container)
    fake = container.ai.adapter
    assert isinstance(fake, FakeKnowledgeAI)
    fake.received.clear()
    with app_client(container) as client:
        result = client.post(f"/api/tasks/{task.id}/progress-summary", json={})
        assert result.status_code == 200
        body = result.json()
        assert body["progress_digest"].startswith("요청 사항:") and body["progress_stale"] is False
        sent = " ".join(item.value for item in fake.received)
        for leaked in (BODY_MARKER, SENDER, ADDRESS, "hong.synthetic"):
            assert leaked not in sent
        # 새 메일이 오면 '새 메일 있음 · 다시 요약'
        clock.advance(timedelta(hours=1))
        _ingest(container, clock, _msg(5, clock.now() - timedelta(minutes=1)))
        detail = client.get(f"/api/tasks/{task.id}").json()
        assert detail["progress_stale"] is True and detail["progress_digest"]
        # 속도 제한(업무별 1분 3회)
        codes = [
            client.post(f"/api/tasks/{task.id}/progress-summary", json={}).status_code
            for _ in range(4)
        ]
        assert codes[-1] == 429


def test_todo01_progress_summary_rejects_unknown_and_mailless(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = make_container(tmp_path, clock)
    manual = SqliteTaskRepository(container.db).add(
        Task("수동 업무", TaskStatus.WAITING, TaskSource.MANUAL, clock.now())
    )
    with app_client(container) as client:
        assert client.post("/api/tasks/999999/progress-summary", json={}).status_code == 404
        assert client.post(f"/api/tasks/{manual.id}/progress-summary", json={}).status_code == 400
        assert client.get("/api/tasks/999999/timeline").status_code == 404


# ---- R4: 요약 상태 ----
def _summary(container: Container, ai: Any) -> SummaryService:
    return SummaryService(
        container.tasks, SqliteMailRefRepository(container.db), container.mail, ai
    )


def _state(container: Container, task: Task) -> str:
    with app_client(container) as client:
        row = next(t for t in client.get("/api/tasks").json() if t["id"] == task.id)
    return str(row["summary_state"])


def test_todo02_summary_state_done_and_followups(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    _ingest(container, clock, *_three(clock))
    task = _thread_task(container)
    assert _state(container, task) == "pending"
    service = _summary(container, FakeKnowledgeAI())
    service.run(max_batches=20)
    assert _state(container, task) == "done"
    title_after_first = _thread_task(container).title
    service.run(max_batches=20)  # 제목 요약이 끝나면 받은 후속 메일을 요약한다(제목은 그대로)
    refs = SqliteMailRefRepository(container.db).list_for_task(task.id or 0)
    assert [r.summary_state for r in refs] == [
        "done",
        "done",
        "pending",
    ]  # 보낸 메일은 AI 요약 안 함
    assert _thread_task(container).title == title_after_first


def test_todo02_summary_state_kept_and_missing(tmp_path: Path, clock: FixedClock) -> None:
    class NeverParses:
        def summarize_requests(self, items: Any) -> list[None]:
            return [None] * len(items)

    container = make_container(tmp_path, clock)
    _ingest(container, clock, *_three(clock))
    task = _thread_task(container)
    service = _summary(container, NeverParses())
    for _ in range(MAX_SUMMARY_TRIES):
        service.run(max_batches=20)
    assert _state(container, task) == "kept"

    other = make_container(tmp_path / "b", clock)
    _ingest(other, clock, *_three(clock))
    collector: Any = other.mail.collector
    collector.delete("SYN-1")
    _summary(other, FakeKnowledgeAI()).run(max_batches=20)
    assert _state(other, _thread_task(other)) == "missing"


def test_todo02_summary_state_running_is_synthesized(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    _ingest(container, clock, *_three(clock))
    task = _thread_task(container)
    ref = min(
        SqliteMailRefRepository(container.db).list_for_task(task.id or 0),
        key=lambda r: r.received_at,
    )
    service = container.summary_job._service
    assert ref.id is not None and service._claim(ref.id, task.id)
    try:
        assert _state(container, task) == "running"
    finally:
        service._release([ref.id])
    assert _state(container, task) == "pending"


def test_todo02_migration_computes_summary_state(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    Database(path).close()
    conn = sqlite3.connect(path)
    # 스키마 3 상태를 흉내 낸다: 요약 상태 열이 없던 DB
    conn.executescript(
        """
        ALTER TABLE mail_ref DROP COLUMN summary_state;
        INSERT INTO task (id, title, status, source, created_at)
          VALUES (1, 'AI 제목', 'suggested', 'mail', '2026-11-16T00:00:00.000000+00:00');
        INSERT INTO mail_ref (source, entry_id, message_id, thread_id, received_at, summary,
          task_id, digest) VALUES
          ('outlook', 'E1', 'm1', 't', '2026-11-16T00:00:00.000000+00:00', NULL, 1, NULL),
          ('outlook', 'E2', 'm2', 't', '2026-11-16T01:00:00.000000+00:00', 'AI 제목', 1, '요청'),
          ('outlook', 'E3', 'm3', 't', '2026-11-16T02:00:00.000000+00:00', '메일 제목', 1, NULL);
        """
    )
    conn.commit()
    conn.close()
    rows = Database(path).query("SELECT summary_state FROM mail_ref ORDER BY id")
    assert [r["summary_state"] for r in rows] == ["pending", "done", "kept"]
