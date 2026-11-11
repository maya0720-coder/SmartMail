"""메일 수집 테스트(MAIL-01~10). 합성 메일과 가짜 수집기·가짜 COM 개체만 쓴다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from collectors.demo_data import demo_messages
from collectors.detect import CollectorKind, detect_collector
from collectors.fake import FakeMailCollector
from collectors.outlook_com import OutlookComCollector
from config.rules import load_mail_rules
from config.settings import ConfigError
from domain.calendar import week_range, week_start
from domain.clock import KST, FixedClock
from domain.mail import MailKey, MailMessage
from domain.mail_rules import ExclusionRules
from domain.mail_service import MailService
from domain.models import MailSource, TaskEventType
from storage.db import Database
from storage.repositories import SqliteEventRecorder, SqliteMailRefRepository


def _msg(n: int, when: datetime, thread: str = "T", **kw: Any) -> MailMessage:
    values: dict[str, Any] = {
        "key": MailKey(MailSource.OUTLOOK, f"E{n}", f"<m{n}@x.invalid>"),
        "thread_id": thread,
        "subject": f"요청 {n}",
        "sender_name": "김가상",
        "received_at": when,
        "folder": "받은 편지함",
        "body": f"본문-비밀-{n}",
    }
    values.update(kw)
    return MailMessage(**values)


def _service(db: Database, clock: FixedClock, collector: FakeMailCollector) -> MailService:
    return MailService(
        collector,
        SqliteMailRefRepository(db),
        SqliteEventRecorder(db, clock),
        load_mail_rules(),
        clock,
    )


def test_mail04_week_is_thursday_to_wednesday() -> None:
    wed_late = datetime(2026, 11, 11, 23, 59, 59, tzinfo=KST)
    thu_start = datetime(2026, 11, 12, 0, 0, tzinfo=KST)
    assert week_start(wed_late) == datetime(2026, 11, 5, tzinfo=KST)
    assert week_start(thu_start) == thu_start
    start, end = week_range(thu_start)
    assert end - start == timedelta(days=7)
    assert week_start(datetime(2026, 11, 11, 15, 0, tzinfo=UTC)) == thu_start  # UTC→KST 변환


def test_mail04_collection_period_boundaries(db: Database, clock: FixedClock) -> None:
    start, end = week_range(clock.now())
    collector = FakeMailCollector(
        [
            _msg(1, start - timedelta(seconds=1), "A"),  # 수 23:59:59 이전 주
            _msg(2, start, "B"),  # 목 00:00 포함
            _msg(3, end - timedelta(seconds=1), "C"),  # 수 23:59:59 포함
            _msg(4, end, "D"),  # 다음 주 목 00:00 제외
        ]
    )
    result = _service(db, clock, collector).collect()
    assert sorted(t.thread_id for t in result.threads) == ["B", "C"]


def test_mail03_threads_grouped_and_ordered(db: Database, clock: FixedClock) -> None:
    start, _ = week_range(clock.now())
    collector = FakeMailCollector(
        [
            _msg(2, start + timedelta(hours=2), "T1"),
            _msg(1, start + timedelta(hours=1), "T1"),
            _msg(3, start + timedelta(hours=3), "T1", sent_by_me=True, folder="보낸 편지함"),
        ]
    )
    [thread] = _service(db, clock, collector).collect().threads
    assert [m.key.entry_id for m in thread.messages] == ["E1", "E2", "E3"]


def test_mail05_exclusion_rules(db: Database, clock: FixedClock) -> None:
    start, _ = week_range(clock.now())
    collector = FakeMailCollector(
        [
            _msg(1, start + timedelta(hours=1), "N", subject="[공지] 점검 안내"),
            _msg(2, start + timedelta(hours=2), "A", is_auto=True),
            _msg(3, start + timedelta(hours=3), "S", sender_address="noreply@x.invalid"),
            _msg(4, start + timedelta(hours=4), "F", folder="정크 메일"),
            _msg(5, start + timedelta(hours=5), "OK"),
        ]
    )
    result = _service(db, clock, collector).collect()
    assert [t.thread_id for t in result.threads] == ["OK"]
    assert result.excluded_messages == 4


def test_mail05_custom_sender_rule() -> None:
    rules = ExclusionRules(excluded_senders=("boss@x.invalid",))
    message = _msg(1, datetime.now(UTC), sender_address="BOSS@x.invalid")
    assert rules.excludes(message)
    assert not rules.excludes(_msg(2, datetime.now(UTC), sent_by_me=True, subject="[공지]"))


def test_mail08_body_never_stored(db: Database, clock: FixedClock) -> None:
    start, _ = week_range(clock.now())
    collector = FakeMailCollector([_msg(1, start + timedelta(hours=1), "T")])
    service = _service(db, clock, collector)
    service.collect()
    service.collect()  # 두 번 수집해도 참조는 1건
    rows = db.query("SELECT * FROM mail_ref")
    assert len(rows) == 1
    dumped = repr([tuple(r) for r in db.query("SELECT * FROM mail_ref")])
    assert "본문-비밀" not in dumped and "요청 1" not in dumped
    events = SqliteEventRecorder(db, clock).list_events(kinds=[TaskEventType.RECEIVED])
    assert len(events) == 1


def test_mail09_missing_original(db: Database, clock: FixedClock) -> None:
    start, _ = week_range(clock.now())
    collector = FakeMailCollector([_msg(1, start + timedelta(hours=1), "T")])
    service = _service(db, clock, collector)
    service.collect()
    [ref] = db.query("SELECT id FROM mail_ref")
    view = service.view(ref["id"])
    assert view is not None and not view.missing
    collector.delete("E1")
    view = service.view(ref["id"])
    assert view is not None and view.missing
    assert service.open_in_client(ref["id"]) is False
    assert service.view(9999) is None


def test_mail10_open_in_client(db: Database, clock: FixedClock) -> None:
    start, _ = week_range(clock.now())
    collector = FakeMailCollector([_msg(1, start + timedelta(hours=1), "T")])
    service = _service(db, clock, collector)
    service.collect()
    [ref] = db.query("SELECT id FROM mail_ref")
    assert service.open_in_client(ref["id"]) is True
    assert collector.opened == ["E1"]


def test_mail07_detection_switches_to_groupware() -> None:
    def failing() -> None:
        raise OSError("COM 실패")

    assert detect_collector(failing, "win32").kind is CollectorKind.GROUPWARE
    assert detect_collector(lambda: None, "win32").kind is CollectorKind.OUTLOOK
    assert detect_collector(lambda: None, "linux").kind is CollectorKind.GROUPWARE


def test_rules_file_validation(tmp_path: Path) -> None:
    bad = tmp_path / "rules.toml"
    bad.write_text("subject_keywords = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_mail_rules(bad)
    bad.write_text("unknown = []\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_mail_rules(bad)
    with pytest.raises(ConfigError):
        load_mail_rules(tmp_path / "none.toml")


def test_demo_messages_are_synthetic() -> None:
    messages = demo_messages(datetime(2026, 11, 5, tzinfo=KST))
    assert all(m.sender_address.endswith(".invalid") for m in messages)
    assert len({m.key.message_id for m in messages}) == len(messages)


# ---- 클래식 아웃룩 COM 변환(가짜 COM 개체) ----


class FakeItems:
    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self.Count = len(items)
        self.query = ""

    def Restrict(self, query: str) -> FakeItems:  # noqa: N802 - COM 이름
        self.query = query
        return self

    def Item(self, index: int) -> Any:  # noqa: N802
        return self._items[index - 1]


def _com_item(n: int, sent: bool = False) -> Any:
    when = datetime(2026, 11, 12, 10, 0)  # noqa: DTZ001 - COM은 현지 시각 벽시계 값
    recipient = SimpleNamespace(
        Type=1,
        Address="me@x.invalid",
        PropertyAccessor=SimpleNamespace(GetProperty=lambda prop: "me@x.invalid"),
    )
    headers = "Auto-Submitted: auto-generated\r\n" if n == 2 else ""
    props = {
        "http://schemas.microsoft.com/mapi/proptag/0x1035001F": f"<com{n}@x.invalid>",
        "http://schemas.microsoft.com/mapi/proptag/0x007D001F": headers,
    }
    return SimpleNamespace(
        Class=43,
        EntryID=f"ENTRY{n}",
        ConversationID="CONV1",
        Subject=f"제목{n}",
        SenderName="김가상",
        SenderEmailAddress="kim@x.invalid",
        ReceivedTime=when,
        SentOn=when,
        Body="본문",
        PropertyAccessor=SimpleNamespace(GetProperty=lambda prop: props.get(prop, "")),
        Recipients=SimpleNamespace(Count=1, Item=lambda i: recipient),
        Attachments=SimpleNamespace(Count=1, Item=lambda i: SimpleNamespace(FileName="a.xlsx")),
        Parent=SimpleNamespace(Name="받은 편지함"),
    )


def _fake_outlook() -> Any:
    inbox = SimpleNamespace(Name="받은 편지함", Items=FakeItems([_com_item(1), _com_item(2)]))
    sent = SimpleNamespace(Name="보낸 편지함", Items=FakeItems([_com_item(3, sent=True)]))
    entry = SimpleNamespace(
        Address="me@x.invalid",
        GetExchangeUser=lambda: SimpleNamespace(PrimarySmtpAddress="me@x.invalid"),
    )
    items = {"ENTRY1": _com_item(1)}

    def get_item(entry_id: str) -> Any:
        if entry_id not in items:
            raise OSError("없음")
        return items[entry_id]

    namespace = SimpleNamespace(
        CurrentUser=SimpleNamespace(AddressEntry=entry),
        GetDefaultFolder=lambda fid: inbox if fid == 6 else sent,
        GetItemFromID=get_item,
    )
    return SimpleNamespace(GetNamespace=lambda name: namespace)


def test_mail01_outlook_com_conversion() -> None:
    collector = OutlookComCollector(_fake_outlook)
    try:
        since = datetime(2026, 11, 12, tzinfo=KST)
        [thread] = collector.list_threads(since, since + timedelta(days=7))
        received = [m for m in thread.messages if not m.sent_by_me]
        assert len(thread.messages) == 3
        assert received[0].to_me and received[0].key.message_id == "<com1@x.invalid>"
        assert received[0].attachment_names == ("a.xlsx",)  # MAIL-06 파일명만
        assert any(m.is_auto for m in received)
        assert received[0].received_at.tzinfo is not None
        key = received[0].key
        assert collector.get_message(key) is not None
        missing = MailKey(MailSource.OUTLOOK, "GONE", "<gone@x.invalid>")
        assert collector.get_message(missing) is None
    finally:
        collector.close()


def test_outlook_unavailable_raises() -> None:
    from domain.mail import CollectorUnavailableError

    def broken() -> Any:
        raise OSError("no outlook")

    collector = OutlookComCollector(broken)
    try:
        with pytest.raises(CollectorUnavailableError):
            collector.probe()
    finally:
        collector.close()
