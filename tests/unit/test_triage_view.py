"""제안된 업무 트리아지 2단계 테스트(TODO-01·02, WIKI-01, 결정기록 0015, 명세 8장 ⑩⑪).

합성 데이터만 쓴다. 외부 접속 없음.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from domain.clock import KST, FixedClock
from domain.errors import UserInputError
from domain.models import Task, TaskSource, TaskStatus
from domain.tasks.service import TaskEdit
from domain.tasks.triage_view import is_low_confidence, received_group
from domain.weekly.report import WikiTemplate, classify_week
from fixtures.app_factory import app_client, make_container
from storage.repositories import SqliteTaskRepository

# 2026-11-16(월) 10:00 KST. 이번 주 = 11-12(목) 00:00 ~ 11-19(목) 00:00
NOW = datetime(2026, 11, 16, 10, 0, tzinfo=KST)


# ⑩ 받은 시각 묶음 경계(오늘·어제·이번 주(목~수)·이전)
@pytest.mark.parametrize(
    ("received", "expected"),
    [
        (datetime(2026, 11, 16, 0, 0, tzinfo=KST), "today"),
        (datetime(2026, 11, 16, 9, 59, tzinfo=KST), "today"),
        (datetime(2026, 11, 15, 23, 59, tzinfo=KST), "yesterday"),
        (datetime(2026, 11, 15, 0, 0, tzinfo=KST), "yesterday"),
        (datetime(2026, 11, 14, 23, 59, tzinfo=KST), "week"),
        (datetime(2026, 11, 12, 0, 0, tzinfo=KST), "week"),  # 주 시작(목 00:00)
        (datetime(2026, 11, 11, 23, 59, tzinfo=KST), "older"),  # 지난주 수요일
        (
            datetime(2026, 11, 15, 15, 0, tzinfo=KST) - timedelta(hours=9),
            "yesterday",
        ),  # UTC 표기도 KST로
        (None, "older"),
    ],
)
def test_todo01_received_group_boundaries(received: datetime | None, expected: str) -> None:
    assert received_group(received, NOW) == expected


def test_todo01_received_group_thursday_starts_new_week() -> None:
    thursday = datetime(2026, 11, 19, 9, 0, tzinfo=KST)
    assert received_group(datetime(2026, 11, 18, 9, 0, tzinfo=KST), thursday) == "yesterday"
    assert received_group(datetime(2026, 11, 17, 9, 0, tzinfo=KST), thursday) == "older"


def test_todo01_low_confidence_threshold() -> None:
    assert is_low_confidence(0.69)
    assert not is_low_confidence(0.7)
    assert not is_low_confidence(None)


def _task(title: str, status: TaskStatus, **fields: object) -> Task:
    return Task(title, status, TaskSource.MAIL, NOW - timedelta(days=1), **fields)  # type: ignore[arg-type]


# ⑪ 사용자가 고른 주간보고 위치가 자동 분류보다 우선
def test_wiki01_user_weekly_section_overrides_auto() -> None:
    tasks = [
        _task("자동 계획", TaskStatus.WAITING),
        _task("실적으로 옮김", TaskStatus.WAITING, weekly_section="done"),
        _task("이슈로 옮김", TaskStatus.IN_PROGRESS, weekly_section="issues"),
        _task("넣지 않음", TaskStatus.WAITING, weekly_section="none"),
        _task(
            "완료를 계획으로",
            TaskStatus.DONE,
            closed_at=NOW - timedelta(hours=2),
            weekly_section="planned",
        ),
        _task(
            "지난주 완료",
            TaskStatus.DONE,
            closed_at=NOW - timedelta(days=10),
            weekly_section="done",
        ),
        _task("제안 상태는 제외", TaskStatus.SUGGESTED, weekly_section="done"),
        _task("업무 아님은 제외", TaskStatus.DISMISSED, weekly_section="planned"),
    ]
    draft = classify_week(tasks, NOW, WikiTemplate(item_format="{title}"))
    assert draft.done == ["실적으로 옮김"]
    assert draft.planned == ["자동 계획", "완료를 계획으로"]
    assert draft.issues == ["이슈로 옮김"]


def test_wiki01_weekly_section_rejects_unknown_value(tmp_path: Path) -> None:
    clock = FixedClock(NOW)
    container = make_container(tmp_path, clock)
    task = SqliteTaskRepository(container.db).add(_task("합성 업무", TaskStatus.WAITING))
    assert task.id is not None
    with pytest.raises(UserInputError):
        container.tasks.edit(task.id, TaskEdit(weekly_section="everywhere"))
    with app_client(container) as client:
        bad = client.post(f"/api/tasks/{task.id}/edit", json={"weekly_section": "everywhere"})
        assert bad.status_code == 422


def test_todo01_list_api_groups_and_snoozed_filter(tmp_path: Path) -> None:
    clock = FixedClock(NOW)
    container = make_container(tmp_path, clock)
    repo = SqliteTaskRepository(container.db)
    fresh = repo.add(
        _task(
            "오늘 받은 제안",
            TaskStatus.SUGGESTED,
            requested_at=NOW - timedelta(hours=1),
            confidence=0.5,
        )
    )
    hidden = repo.add(
        _task("미룬 제안", TaskStatus.SUGGESTED, requested_at=NOW - timedelta(days=6))
    )
    assert fresh.id is not None and hidden.id is not None
    with app_client(container) as client:
        assert client.post(f"/api/tasks/{hidden.id}/snooze", json={}).status_code == 200
        visible = client.get("/api/tasks", params={"status": "suggested"}).json()
        assert [(t["id"], t["group"], t["low_confidence"]) for t in visible] == [
            (fresh.id, "today", True)
        ]
        only = client.get("/api/tasks", params={"status": "suggested", "snoozed": "only"}).json()
        assert [(t["id"], t["group"]) for t in only] == [(hidden.id, "older")]
        assert (
            client.get("/api/tasks", params={"status": "suggested", "snoozed": "x"}).status_code
            == 422
        )
        summary = client.get("/api/tasks/suggested/summary").json()
        assert (summary["visible"], summary["snoozed"], summary["decided_today"]) == (1, 1, 1)
