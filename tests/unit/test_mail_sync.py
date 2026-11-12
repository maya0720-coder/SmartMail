"""메일 자동·증분·기간 지정 수집 테스트(MAIL-04 개정, 결정기록 0009). 합성 메일만 쓴다."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.mail_sync import background_sync
from collectors.fake import FakeMailCollector
from config.rules import load_mail_rules
from config.settings import ConfigError, load_settings
from domain.clock import KST, FixedClock
from domain.errors import UserInputError
from domain.mail import CollectorUnavailableError, MailKey, MailMessage
from domain.mail_service import MAIL_SYNC_NAME, SYNC_OVERLAP, MailService
from domain.models import MailSource
from fixtures.app_factory import app_client, make_container
from storage.db import Database
from storage.repositories import SqliteEventRecorder, SqliteMailRefRepository, SqliteSyncState


def _msg(n: int, when: datetime) -> MailMessage:
    return MailMessage(
        key=MailKey(MailSource.OUTLOOK, f"E{n}", f"<s{n}@x.invalid>"),
        thread_id=f"T{n}",
        subject=f"요청 {n}",
        sender_name="김가상",
        received_at=when,
        folder="받은 편지함",
        body="본문",
    )


class RecordingCollector(FakeMailCollector):
    def __init__(self, messages: list[MailMessage]) -> None:
        super().__init__(messages)
        self.calls: list[tuple[datetime, datetime]] = []

    def list_threads(self, since: datetime, until: datetime) -> Any:
        self.calls.append((since, until))
        return super().list_threads(since, until)


class BrokenCollector(FakeMailCollector):
    def list_threads(self, since: datetime, until: datetime) -> Any:
        raise CollectorUnavailableError("아웃룩 메일을 읽지 못했습니다")


def _service(
    db: Database, clock: FixedClock, collector: FakeMailCollector, days: int = 30
) -> MailService:
    return MailService(
        collector,
        SqliteMailRefRepository(db),
        SqliteEventRecorder(db, clock),
        load_mail_rules(),
        clock,
        sync_state=SqliteSyncState(db),
        initial_sync_days=days,
    )


def test_mail04_first_sync_reads_last_30_days(db: Database, clock: FixedClock) -> None:
    now = clock.now()
    collector = RecordingCollector(
        [
            _msg(1, now - timedelta(days=31)),  # 30일 이전: 제외
            _msg(2, now - timedelta(days=29)),
            _msg(3, now - timedelta(hours=1)),
        ]
    )
    service = _service(db, clock, collector)
    assert service.last_synced_at is None
    result = service.sync()
    assert result.initial is True
    assert collector.calls == [(now - timedelta(days=30), now)]
    assert sorted(t.thread_id for t in result.threads) == ["T2", "T3"]
    assert service.last_synced_at == now


def test_mail04_next_sync_is_incremental_and_picks_up_new_mail(
    db: Database, clock: FixedClock
) -> None:
    start = clock.now()
    collector = RecordingCollector([_msg(1, start - timedelta(days=1))])
    service = _service(db, clock, collector)
    service.sync()
    clock.advance(timedelta(minutes=10))
    collector.add(_msg(2, clock.now() - timedelta(minutes=5)))
    result = service.sync()
    assert result.initial is False
    assert collector.calls[-1] == (start - SYNC_OVERLAP, clock.now())
    assert result.new_messages == 1  # 겹치는 구간의 기존 메일은 다시 저장하지 않는다
    assert service.last_synced_at == clock.now()


def test_mail04_sync_failure_keeps_cursor(db: Database, clock: FixedClock) -> None:
    service = _service(db, clock, BrokenCollector([]))
    with pytest.raises(CollectorUnavailableError):
        service.sync()
    assert SqliteSyncState(db).get(MAIL_SYNC_NAME) is None


def test_mail04_initial_days_is_configurable(db: Database, clock: FixedClock) -> None:
    collector = RecordingCollector([])
    _service(db, clock, collector, days=7).sync()
    assert collector.calls[0][0] == clock.now() - timedelta(days=7)
    with pytest.raises(ValueError):
        _service(db, clock, collector, days=0)


def test_mail04_long_idle_sync_is_capped(db: Database, clock: FixedClock) -> None:
    SqliteSyncState(db).set(MAIL_SYNC_NAME, clock.now() - timedelta(days=800))
    collector = RecordingCollector([])
    _service(db, clock, collector).sync()
    assert collector.calls[0][0] == clock.now() - timedelta(days=366)


def test_mail04_manual_range_inclusive_and_cursor_unchanged(
    db: Database, clock: FixedClock
) -> None:
    collector = RecordingCollector(
        [
            _msg(1, datetime(2026, 9, 30, 23, 59, tzinfo=KST)),  # 시작일 전날: 제외
            _msg(2, datetime(2026, 10, 1, 0, 0, tzinfo=KST)),
            _msg(3, datetime(2026, 10, 10, 23, 59, tzinfo=KST)),  # 종료일 당일 포함
            _msg(4, datetime(2026, 10, 11, 0, 0, tzinfo=KST)),
        ]
    )
    service = _service(db, clock, collector)
    result = service.collect_range(date(2026, 10, 1), date(2026, 10, 10))
    assert sorted(t.thread_id for t in result.threads) == ["T2", "T3"]
    assert service.last_synced_at is None  # 수동 수집은 자동 수집 기준 시각을 바꾸지 않는다


@pytest.mark.parametrize(
    ("first", "last"),
    [
        (date(2026, 10, 10), date(2026, 10, 1)),  # 역순
        (date(2026, 11, 1), date(2026, 11, 30)),  # 미래 종료일
        (date(2025, 1, 1), date(2026, 11, 1)),  # 366일 초과
    ],
)
def test_mail04_manual_range_rejects_bad_input(
    db: Database, clock: FixedClock, first: date, last: date
) -> None:
    with pytest.raises(UserInputError):
        _service(db, clock, RecordingCollector([])).collect_range(first, last)


def test_mail04_settings_bounds(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    base = (
        "SMARTMAIL_APP_PORT=8080\nSMARTMAIL_WIKI_BASE_URL=https://w.example.invalid\n"
        "SMARTMAIL_GKNOWLEDGE_BASE_URL=https://g.example.invalid\n"
        "SMARTMAIL_AIGROUND_BASE_URL=https://a.example.invalid\n"
        "SMARTMAIL_GROUPWARE_MAIL_BASE_URL=https://m.example.invalid\n"
        f"SMARTMAIL_LOG_DIR={tmp_path / 'logs'}\n"
    )
    env.write_text(base, encoding="utf-8")
    settings = load_settings(env, environ={})
    assert (settings.mail_initial_days, settings.mail_sync_minutes) == (30, 10)
    env.write_text(base + "SMARTMAIL_MAIL_INITIAL_DAYS=0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(env, environ={})
    env.write_text(base + "SMARTMAIL_MAIL_SYNC_MINUTES=-1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(env, environ={})


def test_mail04_api_sync_status_and_range(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        status = client.get("/api/mail/sync-status").json()
        assert status["last_synced_at"] is None and status["initial_days"] == 30
        first = client.post("/api/mail/collect", json={}).json()
        assert first["initial"] is True and first["suggested"] > 0
        assert client.get("/api/mail/sync-status").json()["last_synced_at"] is not None
        again = client.post("/api/mail/collect", json={"period": "sync"}).json()
        assert again["new_messages"] == 0 and again["suggested"] == 0
        ok = client.post(
            "/api/mail/collect",
            json={"period": "range", "start": "2026-11-01", "end": "2026-11-05"},
        )
        assert ok.status_code == 200
        missing = client.post("/api/mail/collect", json={"period": "range", "start": "2026-11-01"})
        assert missing.status_code == 422
        stray = client.post("/api/mail/collect", json={"period": "sync", "start": "2026-11-01"})
        assert stray.status_code == 422
        reversed_range = client.post(
            "/api/mail/collect",
            json={"period": "range", "start": "2026-11-05", "end": "2026-11-01"},
        )
        assert reversed_range.status_code == 400


def test_mail04_demo_mode_never_touches_real_db(tmp_path: Path, clock: FixedClock) -> None:
    # 회귀: 데모 실행이 실제 DB에 가상 업무·수집 기준 시각을 남겨 첫 30일 수집이 건너뛰어지던 문제
    container = make_container(tmp_path, clock)  # 데모 모드
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
    assert not (tmp_path / "data" / "test.db").exists()


def test_mail04_background_sync_tolerates_unavailable_collector(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = make_container(tmp_path, clock, demo_mode=False)  # 그룹웨어(미연동) 경로
    background_sync(container)  # 예외 없이 끝나야 한다
    assert container.mail.last_synced_at is None
