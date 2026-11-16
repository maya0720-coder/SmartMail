"""대시보드 기간 기준 테스트(A1, MET-01~04·TODO-03, 결정기록 0039). 합성 업무만 쓴다."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from domain.clock import KST, FixedClock
from domain.metrics.aggregate import aggregate, cards, current_cards, current_window
from domain.metrics.backfill import backfill_ledger
from domain.metrics.periods import GRAINS
from domain.models import LedgerEntry, Task, TaskSource, TaskStatus
from domain.tasks.period_view import in_period
from fixtures.app_factory import app_client, make_container
from storage.knowledge_repos import SqliteLedgerRepository
from storage.repositories import SqliteTaskRepository


def _entry(n: int, when: datetime) -> LedgerEntry:
    return LedgerEntry(n, when, "본인", has_due=False, due_met=None)


def _done_today(container: Any, count: int) -> None:
    for n in range(count):
        task = container.tasks.add_manual(f"합성 완료 업무 {n}")
        container.tasks.change_status(task.id, TaskStatus.DONE)


def test_met01_current_basis_counts_today_in_every_grain(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    _done_today(container, 3)
    with app_client(container) as client:
        for grain in GRAINS:
            body = client.get("/api/metrics", params={"grain": grain}).json()
            card = {c["metric"]: c for c in body["cards"]}
            assert body["basis"] == "current" and card["MET-01"]["current"]["value"] == 3
            assert card["MET-01"]["current"]["reference_only"]  # 5건 미만은 참고
            assert body["current_label"].endswith("(진행 중)")
            assert body["compare_label"].endswith("동기 대비")
            assert body["period_start"] <= clock.now().astimezone(KST).isoformat()
        assert client.get("/api/metrics", params={"basis": "all"}).status_code == 422


def test_met01_compare_window_matches_elapsed_days() -> None:
    now = datetime(2026, 11, 17, 15, 0, tzinfo=KST)
    year = current_window(now, "year")
    assert year.start == datetime(2026, 1, 1, tzinfo=KST)
    assert year.compare_start == datetime(2025, 1, 1, tzinfo=KST)
    assert year.compare_end - year.compare_start == now - year.start  # 같은 경과 시간
    assert year.compare_text == "2025.01.01~2025.11.17"
    assert year.period_text == "2026년 · 11/17까지" and year.compare_label == "전년 동기 대비"
    month = current_window(now, "month")
    assert month.compare_text == "2026.10.01~2026.10.17" and month.compare_label == "전월 동기 대비"
    # 직전 기간이 더 짧으면 직전 기간 끝에서 멈춘다(3/31 → 2월 전체)
    short = current_window(datetime(2026, 3, 31, 10, 0, tzinfo=KST), "month")
    assert short.compare_end == datetime(2026, 3, 1, tzinfo=KST)
    assert short.compare_text == "2026.02.01~2026.02.28"
    rows = [
        _entry(1, datetime(2025, 11, 10, tzinfo=KST)),  # 비교 구간 안
        _entry(2, datetime(2025, 11, 20, tzinfo=KST)),  # 비교 구간 밖(경과일 이후)
        _entry(3, datetime(2026, 11, 16, tzinfo=KST)),
        _entry(4, datetime(2026, 11, 17, 14, tzinfo=KST)),
    ]
    card = {c.metric: c for c in current_cards(rows, year)}["MET-01"]
    assert card.current.value == 2 and card.previous.value == 1 and card.delta == 1


def test_met01_last_complete_basis_unchanged(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    _done_today(container, 2)
    with app_client(container) as client:
        body = client.get("/api/metrics", params={"grain": "week", "basis": "last_complete"}).json()
    expected = cards(aggregate(container.ledger.list_entries(), clock.now(), "week"))
    assert body["basis"] == "last_complete"
    assert [c["current"]["value"] for c in body["cards"]] == [c.current.value for c in expected]
    assert body["cards"][0]["current"]["value"] == 0  # 진행 중인 이번 주는 빼는 이전 방식


def _task(
    title: str,
    status: TaskStatus,
    created: datetime,
    closed: datetime | None = None,
) -> Task:
    return Task(title, status, TaskSource.MANUAL, created, closed_at=closed)


def test_todo03_period_rule_and_carry() -> None:
    start = datetime(2026, 11, 1, tzinfo=KST)
    end = datetime(2026, 11, 17, tzinfo=KST)
    old = start - timedelta(days=30)
    new = _task("새 업무", TaskStatus.WAITING, start + timedelta(days=2))
    carried = _task("이월 업무", TaskStatus.IN_PROGRESS, old)
    closed_now = _task("이번 달 완료", TaskStatus.DONE, old, closed=start + timedelta(days=3))
    closed_old = _task("지난 완료", TaskStatus.DONE, old, closed=old + timedelta(days=1))
    dismissed = _task("지난 무시", TaskStatus.DISMISSED, old)
    mailed = _task("메일 오감", TaskStatus.DONE, old, closed=old)
    assert in_period(new, None, start, end, carry=False)
    assert in_period(carried, None, start, end, carry=True)
    assert not in_period(carried, None, start, end, carry=False)
    assert in_period(closed_now, None, start, end, carry=False)
    assert not in_period(closed_old, None, start, end, carry=True)
    assert not in_period(dismissed, None, start, end, carry=True)
    assert in_period(mailed, start + timedelta(days=5), start, end, carry=False)  # 최근 메일


def _seed_period(container: Any, now: datetime) -> dict[str, int]:
    repo = SqliteTaskRepository(container.db)
    old = now - timedelta(days=60)
    rows = {
        "carried": _task("합성 이월 업무", TaskStatus.WAITING, old),
        "closed_old": _task(
            "합성 지난 완료", TaskStatus.DONE, old, closed=now - timedelta(days=40)
        ),
        "new": _task("합성 새 업무", TaskStatus.WAITING, now - timedelta(hours=1)),
    }
    ids = {}
    for name, task in rows.items():
        saved = repo.add(task)
        assert saved.id is not None
        ids[name] = saved.id
    return ids


def test_todo03_tasks_api_since_and_carry(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    ids = _seed_period(container, clock.now())
    with app_client(container) as client:
        since = client.get("/api/metrics", params={"grain": "month"}).json()["period_start"]
        every = {t["id"] for t in client.get("/api/tasks").json()}
        with_carry = {t["id"] for t in client.get("/api/tasks", params={"since": since}).json()}
        no_carry = client.get("/api/tasks", params={"since": since, "carry": "false"}).json()
        assert set(ids.values()) <= every  # '전체'는 기존 동작
        assert with_carry == {ids["carried"], ids["new"]}
        assert {t["id"] for t in no_carry} == {ids["new"]}
        naive = client.get("/api/tasks", params={"since": "2026-11-01T00:00:00"})
        assert naive.status_code == 400
        assert client.get("/api/tasks", params={"since": "어제"}).status_code == 422


def test_met01_ledger_backfill_explains_donut_vs_card(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    ids = _seed_period(container, clock.now())
    _done_today(container, 1)
    ledger = SqliteLedgerRepository(container.db)
    tasks = container.tasks.list_tasks([TaskStatus.DONE])
    assert backfill_ledger(tasks, ledger, "본인") == 1  # 원장에 없던 지난 완료 1건
    assert backfill_ledger(tasks, ledger, "본인") == 0  # 다시 해도 늘지 않음
    row = next(e for e in ledger.list_entries() if e.task_id == ids["closed_old"])
    assert row.completed_at == clock.now() - timedelta(days=40)
    stored = [tuple(r) for r in container.db.query("SELECT * FROM metric_ledger")]
    assert "합성" not in str(stored)  # 숫자 항목만(제목 없음)
    with app_client(container) as client:
        card = client.get("/api/metrics", params={"grain": "month"}).json()["cards"][0]
        donut_done = [t for t in client.get("/api/tasks").json() if t["status"] == "done"]
    # 도넛 '완료'(상태 기준)는 2건, 카드 '완료 건수'(이번 달 완료 확정)는 1건: 정의대로 다르다
    assert len(donut_done) == 2 and card["current"]["value"] == 1


@pytest.mark.parametrize("value", ["all", "year", "week"])
def test_met01_dashboard_grain_pref_saved(tmp_path: Path, clock: FixedClock, value: str) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        saved = client.put("/api/ui-prefs", json={"dashboard_grain": value})
        assert saved.status_code == 200 and saved.json()["dashboard_grain"] == value
        assert f'data-g="{value}" class="on"' in client.get("/").text
        assert client.put("/api/ui-prefs", json={"dashboard_grain": "day"}).status_code == 422
