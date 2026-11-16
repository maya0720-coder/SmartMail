"""주간보고 마감 알림 테스트(U6, WIKI-03, 결정기록 0037)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from domain.calendar import week_start
from domain.clock import KST, FixedClock
from domain.weekly.remind import due_at, parse_remind
from fixtures.app_factory import app_client, make_container, make_settings

TUE = datetime(2026, 11, 24, 10, 0, tzinfo=KST)  # 주(11/19 목 ~ 11/25 수)


def test_wiki03_parse_remind() -> None:
    assert parse_remind("") is None
    rule = parse_remind("wed 15:00")
    assert rule is not None and (rule.weekday, rule.hour, rule.minute) == (2, 15, 0)
    for bad in ("WEDS 15:00", "WED 25:00", "WED 1500", "수 15:00"):
        with pytest.raises(ValueError):
            parse_remind(bad)


def test_wiki03_due_moves_to_previous_business_day() -> None:
    rule = parse_remind("WED 15:00")
    assert rule is not None
    assert due_at(rule, TUE, frozenset()) == datetime(2026, 11, 25, 15, 0, tzinfo=KST)
    # 공휴일이면 직전 근무일
    holiday = frozenset({date(2026, 11, 25)})
    assert due_at(rule, TUE, holiday) == datetime(2026, 11, 24, 15, 0, tzinfo=KST)
    # 주말 지정은 금요일로
    sat = parse_remind("SAT 09:00")
    assert sat is not None
    assert due_at(sat, TUE, frozenset()) == datetime(2026, 11, 20, 9, 0, tzinfo=KST)


def test_wiki03_settings_reject_bad_format(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_settings(tmp_path, weekly_remind_at="WEDNESDAY 3PM")
    assert make_settings(tmp_path, weekly_remind_at="FRI 17:30").weekly_remind_at == "FRI 17:30"


def test_wiki03_banner_states(tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 11, 25, 14, 59, tzinfo=KST))
    container = make_container(tmp_path, clock, weekly_remind_at="WED 15:00")
    with app_client(container) as client:
        before = client.get("/api/weekly/reminder").json()
        assert before["show"] is False and before["reason"] == "before_due"
        clock.advance(timedelta(minutes=2))
        assert client.get("/api/weekly/reminder").json()["show"] is True
        hidden = client.post("/api/weekly/reminder/hide", json={}).json()
        assert hidden["show"] is False and hidden["reason"] == "hidden_today"
        clock.advance(timedelta(hours=1))
        assert client.get("/api/weekly/reminder").json()["show"] is False
        # 다음 날(같은 주 목요일 전)에는 숨김이 풀린다
        clock.advance(timedelta(hours=24))
        assert client.get("/api/weekly/reminder").json()["reason"] == "before_due"


def test_wiki03_banner_hidden_after_publish(tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 11, 25, 16, 0, tzinfo=KST))
    container = make_container(tmp_path, clock, weekly_remind_at="WED 15:00")
    with app_client(container) as client:
        assert client.get("/api/weekly/reminder").json()["show"] is True
        container.weekly_cache.add(
            week_start(clock.now()), "wiki", "clipboard", None, clock.now(), "합성"
        )
        state = client.get("/api/weekly/reminder").json()
        assert state["show"] is False and state["reason"] == "published"


def test_wiki03_reminder_off_by_default(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        assert client.get("/api/weekly/reminder").json() == {
            "show": False,
            "due_at": None,
            "reason": "off",
        }
