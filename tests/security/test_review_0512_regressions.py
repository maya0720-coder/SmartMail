"""05~12단계 보안 검토 지적 회귀 테스트(결정기록 0007)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from domain.clock import FixedClock
from domain.knowledge import MaskedDraft
from domain.models import Task, TaskSource, TaskStatus
from domain.tasks.extract import DUE_MD, DUE_WEEKDAY, parse_due
from fixtures.app_factory import app_client, make_container
from security.links import safe_link
from security.masking import mask
from storage.db import Database
from storage.repositories import SqliteTaskRepository
from storage.retention import RetentionJob


@pytest.mark.parametrize("text", [" " * 50_000, "1/1" + " " * 50_000, "\n" * 50_000])
def test_m1_due_regex_linear(text: str) -> None:
    started = time.perf_counter()
    DUE_WEEKDAY.search(text)
    DUE_MD.search(text)
    parse_due(text, datetime.now(UTC))
    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize(
    "link",
    [
        "javascript://gk.example.invalid/%0aalert(1)",
        "http://gk.example.invalid/k/1",
        "https://gk.example.invalid:8443/k/1",
        "https://user@gk.example.invalid/k/1",
        "https://evil.example/k/1",
        "data:text/html,x",
    ],
)
def test_m2_unsafe_links_rejected(link: str) -> None:
    assert safe_link(link, ["https://gk.example.invalid"]) is None


def test_m2_safe_links_allowed() -> None:
    base = "https://gk.example.invalid/"
    assert safe_link("/k/1", [base], base_for_relative=base) == "https://gk.example.invalid/k/1"
    assert safe_link("https://GK.example.invalid:443/k/2", [base])


def test_m4_evidence_not_leaked_after_wipe(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={"period": "recent7"})
        first = client.get("/api/tasks").json()[0]
        assert client.get(f"/api/tasks/{first['id']}").json()["evidence"]
        client.post("/api/data/wipe", json={"confirm": True})
        new = client.post("/api/tasks", json={"title": "수동 업무"}).json()
        assert new["id"] != first["id"]  # AUTOINCREMENT: ID 재사용 없음
        assert client.get(f"/api/tasks/{new['id']}").json()["evidence"] is None


def test_m5_unhandled_suggestions_purged(db: Database, clock: FixedClock) -> None:
    repo = SqliteTaskRepository(db)
    repo.add(Task("오래된 제안", TaskStatus.SUGGESTED, TaskSource.MAIL, clock.now() - timedelta(8)))
    repo.add(Task("새 제안", TaskStatus.SUGGESTED, TaskSource.MAIL, clock.now()))
    RetentionJob(db, clock, retention_days=7, keep_ledger=False).run()
    assert [t.title for t in repo.list_tasks()] == ["새 제안"]


def test_m6_task_title_masked_on_save(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        task = client.post("/api/tasks", json={"title": "연락처 010-1234-5678 회신 요청"}).json()
    assert "010-1234-5678" not in task["title"]


def test_l2_request_size_and_tag_length_limits(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        big = client.post("/api/tasks", json={"title": "x", "pad": "y" * 300_000})
        assert big.status_code == 413
        task = client.post("/api/tasks", json={"title": "업무"}).json()
        long_tag = {"title": "t", "body": "b", "tags": ["가" * 31]}
        assert client.post(f"/api/gk/register/{task['id']}", json=long_tag).status_code == 422


def test_l3_unknown_task_id_rejected_before_ai(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        search = client.post("/api/search", json={"query": "배치", "task_id": 9999})
        assert search.status_code == 404
        feedback = client.post("/api/search/feedback", json={"task_id": 9999, "helpful": True})
        assert feedback.status_code == 404
        assert client.post("/api/search", json={"query": "x", "task_id": 0}).status_code == 422


def test_l7_masked_draft_rejects_plain_str() -> None:
    with pytest.raises(TypeError):
        MaskedDraft("제목", mask("본문"), ())  # type: ignore[arg-type]
