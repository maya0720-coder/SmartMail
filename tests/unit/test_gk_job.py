"""G-Knowledge 백그라운드 등록 큐 테스트(N2, GK-03·04·06, 결정기록 0035).

가짜 G-Knowledge 저장소만 쓴다(외부 접속 없음). 합성 데이터만 쓴다.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pytest

from app.container import Container
from app.gk_job import GkRegisterQueue, RetryConfirmRequiredError
from domain.clock import FixedClock
from domain.knowledge import DuplicateRegistrationError, MaskedDraft, draft_from_edit
from fixtures.app_factory import app_client, make_container
from fixtures.gk_wait import wait_gk
from fixtures.synthetic import synthetic_rrn
from integrations.gknowledge.fake import FakeKnowledgeStore
from storage.db import Database
from storage.gk_state_repo import SqliteGkStates


def _setup(tmp_path: Path, clock: FixedClock) -> tuple[Container, FakeKnowledgeStore, int]:
    container = make_container(tmp_path, clock)
    store = container.knowledge.store
    assert isinstance(store, FakeKnowledgeStore)
    task = container.tasks.add_manual("출하 I/F 재처리 합성 업무")
    assert task.id is not None
    return container, store, task.id


def _db_text(db: Database) -> str:
    """DB의 모든 표 내용을 글자로 모은다(민감정보가 남지 않았는지 확인용)."""
    names = [r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")]
    rows = [db.query(f"SELECT * FROM {n}") for n in names]  # noqa: S608 - 표 이름은 DB 목록에서
    return "\n".join(str(tuple(r)) for table in rows for r in table)


def _state(queue: GkRegisterQueue, task_id: int) -> str:
    status = queue.status_of(task_id)
    assert status is not None
    return status.state


def _task_id(container: Container, title: str) -> int:
    task_id = container.tasks.add_manual(title).id
    assert task_id is not None
    return task_id


def _draft(body: str = "합성 본문") -> MaskedDraft:
    return draft_from_edit("출하 I/F 재처리", body, ["SD"])


def test_gk03_states_queued_running_requested(tmp_path: Path, clock: FixedClock) -> None:
    container, store, task_id = _setup(tmp_path, clock)
    queue: GkRegisterQueue = container.gk_queue
    store.gate = threading.Event()
    assert queue.enqueue(task_id, _draft()).state == "queued"
    worker = threading.Thread(target=queue.process_next)
    worker.start()
    for _ in range(100):
        status = queue.status_of(task_id)
        if status and status.state == "running" and status.step == "analyze":
            break
        time.sleep(0.01)
    assert status is not None and (status.state, status.step) == ("running", "analyze")
    assert queue.active_count() == 1
    store.gate.set()
    worker.join(timeout=5)
    final = queue.status_of(task_id)
    assert final is not None and final.state == "requested"
    assert container.tasks.get(task_id).gk_registered
    rows = SqliteGkStates(container.db).all()
    assert [(r.task_id, r.state) for r in rows] == [(task_id, "requested")]


def test_gk06_unsent_failure_retry_registers_once(tmp_path: Path, clock: FixedClock) -> None:
    """등록요청 전 실패(분석 시간 초과)는 failed_unsent. 다시 시도하면 1회만 등록된다."""
    container, store, task_id = _setup(tmp_path, clock)
    queue: GkRegisterQueue = container.gk_queue
    store.register_fail = "unsent"
    queue.enqueue(task_id, _draft())
    queue.process_next()
    failed = queue.status_of(task_id)
    assert failed is not None and (failed.state, failed.reason) == (
        "failed_unsent",
        "analyze_timeout",
    )
    assert store.items == []
    store.register_fail = None
    queue.enqueue(task_id, _draft())
    queue.process_next()
    assert _state(queue, task_id) == "requested"
    assert len(store.items) == 1


def test_gk06_unknown_result_no_auto_retry(tmp_path: Path, clock: FixedClock) -> None:
    """등록요청 뒤 결과 미확인은 unknown. 자동 재시도 없음, 다시 보내려면 확인이 필요하다."""
    container, store, task_id = _setup(tmp_path, clock)
    queue: GkRegisterQueue = container.gk_queue
    store.register_fail = "unknown"
    queue.enqueue(task_id, _draft())
    queue.process_next()
    assert _state(queue, task_id) == "unknown"
    assert queue.process_next() is False  # 큐가 비어 있음(자동 재시도 없음)
    assert store.register_calls == 1
    with pytest.raises(RetryConfirmRequiredError):
        queue.enqueue(task_id, _draft())
    # 사용자가 G-Knowledge에서 확인한 뒤 '등록됨으로 표시'
    assert queue.mark_registered(task_id).state == "requested"
    assert container.tasks.get(task_id).gk_registered
    with pytest.raises(DuplicateRegistrationError):
        queue.enqueue(task_id, _draft(), retry_unknown=True)


def test_gk06_same_task_twice_rejected(tmp_path: Path, clock: FixedClock) -> None:
    container, _store, task_id = _setup(tmp_path, clock)
    queue: GkRegisterQueue = container.gk_queue
    queue.enqueue(task_id, _draft())
    with pytest.raises(DuplicateRegistrationError):
        queue.enqueue(task_id, _draft())
    with pytest.raises(ValueError):
        queue.mark_registered(task_id)  # 확인 필요 건이 아니면 표시할 수 없다


def test_gk03_restart_cleans_leftover_running(tmp_path: Path, clock: FixedClock) -> None:
    """앱을 다시 시작하면 queued·running이 정리된다(등록요청 단계였으면 unknown)."""
    container, _store, first = _setup(tmp_path, clock)
    second = _task_id(container, "합성 업무 2")
    third = _task_id(container, "합성 업무 3")
    repo = SqliteGkStates(container.db)
    repo.set(first, "queued", clock.now())
    repo.set(second, "running", clock.now(), "analyze")
    repo.set(third, "running", clock.now(), "submit")
    queue = GkRegisterQueue(container.knowledge, repo, clock)
    states = {i: (s.state, s.reason) for i, s in queue.statuses([first, second, third]).items()}
    assert states == {
        first: ("failed_unsent", "app_restart"),
        second: ("failed_unsent", "app_restart"),
        third: ("unknown", "app_restart"),
    }


def test_gk03_api_accepts_quickly_and_reports_status(tmp_path: Path, clock: FixedClock) -> None:
    """등록 API는 결과를 기다리지 않고 1초 안에 202로 응답한다."""
    container, store, task_id = _setup(tmp_path, clock)
    store.gate = threading.Event()  # 등록요청 직전에서 붙잡는다
    payload = {"title": "출하 I/F 재처리", "body": "합성 본문", "tags": []}
    with app_client(container) as client:
        started = time.monotonic()
        accepted = client.post(f"/api/gk/register/{task_id}", json=payload)
        assert time.monotonic() - started < 1.0
        assert accepted.status_code == 202 and accepted.json()["state"] == "queued"
        assert client.post(f"/api/gk/register/{task_id}", json=payload).status_code == 409
        detail = client.get(f"/api/tasks/{task_id}").json()
        assert detail["gk_state"]["state"] in ("queued", "running")
        store.gate.set()
        assert wait_gk(client, task_id)["state"] == "requested"
        assert client.get("/api/gk/status", params={"ids": "a"}).status_code == 400
        too_many = ",".join(str(i) for i in range(101))
        assert client.get("/api/gk/status", params={"ids": too_many}).status_code == 400
        assert client.post(f"/api/gk/mark-registered/{task_id}", json={}).status_code == 409


def test_gk06_api_unknown_needs_confirm_then_mark(tmp_path: Path, clock: FixedClock) -> None:
    container, store, task_id = _setup(tmp_path, clock)
    store.register_fail = "unknown"
    payload = {"title": "출하 I/F 재처리", "body": "합성 본문", "tags": []}
    with app_client(container) as client:
        client.post(f"/api/gk/register/{task_id}", json=payload)
        assert wait_gk(client, task_id)["state"] == "unknown"
        again = client.post(f"/api/gk/register/{task_id}", json=payload)
        assert again.status_code == 409 and again.json()["code"] == "confirm_required"
        marked = client.post(f"/api/gk/mark-registered/{task_id}", json={})
        assert marked.status_code == 200 and marked.json()["gk_state"]["state"] == "requested"


def test_gk07_queue_and_logs_hold_no_sensitive_text(
    tmp_path: Path, clock: FixedClock, caplog: pytest.LogCaptureFixture
) -> None:
    """초안은 마스킹되어 큐(메모리)에만 있고, DB·로그에는 상태 코드만 남는다."""
    container, store, task_id = _setup(tmp_path, clock)
    rrn = synthetic_rrn()
    store.register_fail = "unsent"
    payload = {"title": "출하 I/F 재처리", "body": f"합성 본문 {rrn}", "tags": []}
    with caplog.at_level(logging.DEBUG), app_client(container) as client:
        client.post(f"/api/gk/register/{task_id}", json=payload)
        wait_gk(client, task_id)
    assert rrn not in caplog.text
    dump = _db_text(container.db)
    assert rrn not in dump and "합성 본문" not in dump
