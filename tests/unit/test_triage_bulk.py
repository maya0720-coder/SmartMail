"""제안 일괄 처리 API 테스트(U2, TODO-02, 결정기록 0036). 합성 데이터만 쓴다."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from domain.clock import FixedClock
from domain.models import Task, TaskSource, TaskStatus
from fixtures.app_factory import app_client, make_container
from storage.repositories import SqliteTaskRepository

COMPARED = ("status", "title", "module_tag", "due_at", "closed_at", "snoozed", "weekly_section")


def _suggested(container: Any, clock: FixedClock, title: str) -> int:
    repo = SqliteTaskRepository(container.db)
    task = repo.add(
        Task(title, TaskStatus.SUGGESTED, TaskSource.MAIL, clock.now(), module_tag="일반업무")
    )
    assert task.id is not None
    return task.id


def _view(body: dict[str, Any]) -> dict[str, Any]:
    return {key: body[key] for key in COMPARED}


@pytest.mark.parametrize("decision", ["approve", "snooze", "dismiss"])
def test_todo02_bulk_result_equals_single_decisions(
    tmp_path: Path, clock: FixedClock, decision: str
) -> None:
    """같은 제안을 건별로 결정한 결과와 일괄 결정한 결과가 같다."""
    single = make_container(tmp_path / "single", clock)
    bulk = make_container(tmp_path / "bulk", clock)
    titles = ["합성 제안 1", "합성 제안 2", "합성 제안 3"]
    single_ids = [_suggested(single, clock, t) for t in titles]
    bulk_ids = [_suggested(bulk, clock, t) for t in titles]
    with app_client(single) as client:
        for task_id in single_ids:
            assert client.post(f"/api/tasks/{task_id}/{decision}", json={}).status_code == 200
        expected = [_view(client.get(f"/api/tasks/{i}").json()) for i in single_ids]
    with app_client(bulk) as client:
        body = client.post("/api/triage/bulk", json={"ids": bulk_ids, "decision": decision}).json()
        assert body["succeeded"] == bulk_ids and body["failed"] == []
        assert body["bulk_token"]
        actual = [_view(client.get(f"/api/tasks/{i}").json()) for i in bulk_ids]
    assert actual == expected


def test_todo02_bulk_reports_partial_failures(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    first = _suggested(container, clock, "합성 제안 A")
    done = _suggested(container, clock, "합성 제안 B")
    container.tasks.approve(done, None)  # 이미 할 일로 보낸 건
    with app_client(container) as client:
        body = client.post(
            "/api/triage/bulk", json={"ids": [first, done, 999999], "decision": "approve"}
        ).json()
    assert body["succeeded"] == [first]
    failed = {f["id"]: f["error"] for f in body["failed"]}
    assert set(failed) == {done, 999999}
    assert "제안 상태" in failed[done] and "찾을 수 없" in failed[999999]


def test_todo02_bulk_revert_restores_all_once(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    ids = [_suggested(container, clock, f"합성 제안 {n}") for n in range(12)]
    with app_client(container) as client:
        before = [_view(client.get(f"/api/tasks/{i}").json()) for i in ids]
        token = client.post("/api/triage/bulk", json={"ids": ids, "decision": "approve"}).json()[
            "bulk_token"
        ]
        clock.advance(timedelta(seconds=30))
        reverted = client.post("/api/triage/bulk/revert", json={"bulk_token": token})
        assert reverted.status_code == 200
        assert reverted.json() == {"reverted": ids, "failed": []}
        after = [_view(client.get(f"/api/tasks/{i}").json()) for i in ids]
        assert after == before
        # 일회용
        again = client.post("/api/triage/bulk/revert", json={"bulk_token": token})
        assert again.status_code == 409


def test_todo02_bulk_revert_expires(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    ids = [_suggested(container, clock, f"합성 제안 {n}") for n in range(2)]
    with app_client(container) as client:
        token = client.post("/api/triage/bulk", json={"ids": ids, "decision": "dismiss"}).json()[
            "bulk_token"
        ]
        clock.advance(timedelta(seconds=61))
        assert client.post("/api/triage/bulk/revert", json={"bulk_token": token}).status_code == 409
        bad = client.post("/api/triage/bulk/revert", json={"bulk_token": "<x>"})
        assert bad.status_code == 422


def test_todo02_bulk_input_validation(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    task_id = _suggested(container, clock, "합성 제안")
    with app_client(container) as client:
        too_many = client.post(
            "/api/triage/bulk", json={"ids": list(range(1, 102)), "decision": "approve"}
        )
        assert too_many.status_code == 422  # 101건은 거부
        assert (
            client.post("/api/triage/bulk", json={"ids": [], "decision": "approve"}).status_code
            == 422
        )
        for bad in (
            {"ids": [task_id], "decision": "delete"},
            {"ids": [0], "decision": "approve"},
            {"ids": ["x"], "decision": "approve"},
            {"ids": [task_id], "decision": "approve", "status": "done"},  # 대량 할당 차단
            {"ids": [task_id], "decision": "category"},  # 구분 없이 구분 변경
            {"ids": [task_id], "decision": "approve", "module_tag": "전자결재"},
        ):
            assert client.post("/api/triage/bulk", json=bad).status_code == 422, bad
        # 100건은 허용(없는 번호는 실패 목록으로)
        ok = client.post(
            "/api/triage/bulk",
            json={"ids": [task_id, *range(10**6, 10**6 + 99)], "decision": "snooze"},
        ).json()
        assert ok["succeeded"] == [task_id] and len(ok["failed"]) == 99
    assert container.tasks.get(task_id).status is TaskStatus.SUGGESTED


def test_todo02_bulk_category_change(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    ids = [_suggested(container, clock, f"합성 제안 {n}") for n in range(3)]
    with app_client(container) as client:
        body = client.post(
            "/api/triage/bulk",
            json={"ids": ids, "decision": "category", "module_tag": "전자결재"},
        ).json()
        assert body["succeeded"] == ids and body["bulk_token"] is None
        unknown = client.post(
            "/api/triage/bulk",
            json={"ids": ids[:1], "decision": "category", "module_tag": "없는구분"},
        ).json()
        assert unknown["succeeded"] == [] and len(unknown["failed"]) == 1
    for task_id in ids:
        task = container.tasks.get(task_id)
        assert task.module_tag == "전자결재" and task.status is TaskStatus.SUGGESTED


def test_todo02_bulk_dismiss_learns_per_item_and_revert_unlearns(
    tmp_path: Path, clock: FixedClock
) -> None:
    """'업무 아님' 학습은 건별로 기록되고, 일괄 되돌리기 때 건별로 줄어든다(자동 제외 없음 유지)."""
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        ids = [t.id for t in container.tasks.list_tasks([TaskStatus.SUGGESTED])][:2]
        assert len(ids) == 2
        token = client.post("/api/triage/bulk", json={"ids": ids, "decision": "dismiss"}).json()[
            "bulk_token"
        ]
        assert client.get("/api/triage-rules").json()["rules"] >= 1
        client.post("/api/triage/bulk/revert", json={"bulk_token": token})
        assert client.get("/api/triage-rules").json()["rules"] == 0
