"""회신 대기·팔로업 테스트(U7, TODO-05·07, 결정기록 0038). 합성 메일만 쓴다."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.container import Container
from domain.calendar import business_days_after
from domain.clock import KST, FixedClock
from domain.mail import MailKey, MailMessage
from domain.models import MailSource, Task, TaskStatus
from domain.tasks.followup import FollowupPolicy, asks_reply, followup_draft
from fixtures.app_factory import app_client, make_container, make_settings
from storage.repositories import SqliteTaskRepository

MON = datetime(2026, 11, 16, 10, 0, tzinfo=KST)
THREAD = "SYN-FOLLOW-1"
BODY_MARKER = "본문표식-원문저장금지"
SENDER = "합성팀 김가상"
ADDRESS = "kim.synthetic@example.invalid"


def _msg(n: int, when: datetime, *, out: bool = False, body: str = "") -> MailMessage:
    values: dict[str, Any] = {
        "key": MailKey(MailSource.OUTLOOK, f"SYN-F{n}", f"<syn-f{n}@example.invalid>"),
        "thread_id": THREAD,
        "subject": ("RE: " if n > 1 else "") + "[요청] 합성 정산 자료",
        "sender_name": "본인" if out else SENDER,
        "sender_address": "" if out else ADDRESS,
        "received_at": when,
        "folder": "보낸 편지함" if out else "받은 편지함",
        "sent_by_me": out,
        "to_me": not out,
        "body": body or f"합성 정산 자료 송부 부탁드립니다. {BODY_MARKER}",
    }
    return MailMessage(**values)


def _ingest(container: Container, clock: FixedClock, *messages: MailMessage) -> Task:
    collector: Any = container.mail.collector
    for message in messages:
        collector.add(message)
    result = container.mail.collect(clock.now() - timedelta(days=3), clock.now())
    container.tasks.ingest(result.threads)
    task = SqliteTaskRepository(container.db).find_by_thread(THREAD)
    assert task is not None and task.id is not None
    return task


def _approved(container: Container, clock: FixedClock) -> Task:
    task = _ingest(container, clock, _msg(1, clock.now() - timedelta(hours=3)))
    assert task.id is not None
    return container.tasks.approve(task.id)


def test_todo05_my_question_sets_awaiting_and_reply_clears(tmp_path: Path) -> None:
    clock = FixedClock(MON)
    container = make_container(tmp_path, clock)
    _approved(container, clock)
    question = _msg(2, clock.now() - timedelta(hours=1), out=True, body="자료 언제 가능할까요?")
    task = _ingest(container, clock, question)
    assert task.awaiting_reply_since == question.received_at
    assert container.followup.state(task, clock.now()).state == "waiting"
    # 상대 회신이 오면 자동 해제
    clock.advance(timedelta(hours=2))
    task = _ingest(container, clock, _msg(3, clock.now() - timedelta(minutes=5)))
    assert task.awaiting_reply_since is None
    assert container.followup.state(task, clock.now()).state == "none"


def test_todo05_my_mail_without_question_is_not_awaiting(tmp_path: Path) -> None:
    clock = FixedClock(MON)
    container = make_container(tmp_path, clock)
    _approved(container, clock)
    thanks = _msg(
        2, clock.now() - timedelta(hours=1), out=True, body="자료 잘 받았습니다. 감사합니다."
    )
    task = _ingest(container, clock, thanks)
    assert task.awaiting_reply_since is None
    assert asks_reply("RE: 일정", "검토 부탁드립니다") and not asks_reply("RE: 감사", "감사합니다")


def test_todo07_followup_after_business_days_skips_weekend_and_holiday(tmp_path: Path) -> None:
    fri = datetime(2026, 11, 20, 17, 0, tzinfo=KST)
    task = Task("합성", TaskStatus.WAITING, MailSource.OUTLOOK, fri, awaiting_reply_since=fri)  # type: ignore[arg-type]
    policy = FollowupPolicy(days=2)
    assert business_days_after(fri, fri + timedelta(days=3)) == 1  # 토·일 제외, 월 1일
    assert policy.state(task, fri + timedelta(days=3)).state == "waiting"
    assert policy.state(task, fri + timedelta(days=4)).state == "followup"  # 화요일
    holiday = FollowupPolicy(days=2, holidays=frozenset({date(2026, 11, 23)}))
    assert holiday.state(task, fri + timedelta(days=4)).state == "waiting"  # 월요일 공휴일
    assert holiday.state(task, fri + timedelta(days=5)).business_days == 2
    # 해제했거나 완료·제안 상태면 표시하지 않는다
    assert (
        policy.state(replace(task, followup_dismissed=True), fri + timedelta(days=9)).state
        == "none"
    )
    assert (
        policy.state(replace(task, status=TaskStatus.DONE), fri + timedelta(days=9)).state == "none"
    )
    assert (
        policy.state(replace(task, status=TaskStatus.SUGGESTED), fri + timedelta(days=9)).state
        == "none"
    )


def test_todo07_dismiss_stays_until_new_question(tmp_path: Path) -> None:
    clock = FixedClock(MON)
    container = make_container(tmp_path, clock)
    _approved(container, clock)
    first = _msg(2, clock.now() - timedelta(hours=2), out=True, body="확인 부탁드립니다")
    task = _ingest(container, clock, first)
    assert task.id is not None
    container.tasks.clear_awaiting(task.id)
    # 같은 메일로 다시 수집해도 해제 상태 유지
    task = _ingest(container, clock)
    assert task.followup_dismissed and container.followup.state(task, clock.now()).state == "none"
    # 새로 보낸 질문 메일이면 다시 켜진다
    second = _msg(
        4, clock.now() - timedelta(minutes=10), out=True, body="혹시 일정 알려 주실 수 있을까요?"
    )
    task = _ingest(container, clock, second)
    assert task.awaiting_reply_since == second.received_at and not task.followup_dismissed


def test_todo07_api_mark_clear_and_draft(tmp_path: Path) -> None:
    clock = FixedClock(MON)
    container = make_container(tmp_path, clock)
    task = _approved(container, clock)
    suggested = container.tasks.add_manual("합성 수동 업무")
    with app_client(container) as client:
        marked = client.post(f"/api/tasks/{task.id}/awaiting", json={}).json()
        assert marked["followup"]["state"] == "waiting" and marked["followup"]["days"] == 0
        clock.advance(timedelta(days=2))
        row = client.get(f"/api/tasks/{task.id}").json()
        assert row["followup"]["state"] == "followup" and row["followup"]["days"] == 2
        text = client.get(f"/api/tasks/{task.id}/followup-draft").json()["text"]
        assert "2영업일 전" in text and task.title[:20] in text
        for leaked in (BODY_MARKER, SENDER, ADDRESS, "@"):
            assert leaked not in text  # 원문·이름·주소 없음
        cleared = client.post(f"/api/tasks/{task.id}/awaiting/clear", json={}).json()
        assert cleared["followup"]["state"] == "none"
        again = client.post(f"/api/tasks/{task.id}/awaiting/clear", json={})
        assert again.status_code == 409
        container.tasks.change_status(suggested.id or 0, TaskStatus.DONE)
        assert client.post(f"/api/tasks/{suggested.id}/awaiting", json={}).status_code == 409
        assert client.get("/api/tasks/999999/followup-draft").status_code == 404


def test_todo07_no_send_api(tmp_path: Path) -> None:
    """메일 발송 경로가 없다(문안 복사만). 수집기에도 보내기 기능이 없다."""
    clock = FixedClock(MON)
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        paths = [getattr(r, "path", "") for r in client.app.routes]  # type: ignore[attr-defined]
    assert paths and not [p for p in paths if "send" in p.lower() or "reply" in p.lower()]
    collector = container.mail.collector
    assert not [name for name in dir(collector) if "send" in name.lower()]


def test_todo07_draft_masks_title() -> None:
    text = followup_draft("합성 문의 010-1234-5678 확인", 3)
    assert "010-1234-5678" not in text and "3영업일 전" in text


def test_todo07_setting_range(tmp_path: Path) -> None:
    assert make_settings(tmp_path).followup_business_days == 2
    with pytest.raises(ValidationError):
        make_settings(tmp_path, followup_business_days=0)
