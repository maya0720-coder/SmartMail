"""G-Knowledge 등록·검색 테스트(GK-01~07, RET-03). 가짜 저장소·가짜 브라우저만 쓴다."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from config.settings import ConfigError
from domain.clock import FixedClock
from domain.knowledge import (
    DuplicateRegistrationError,
    KnowledgeUnavailableError,
    draft_from_edit,
)
from domain.knowledge_service import KnowledgeService
from domain.models import HistoryCard, TaskStatus
from domain.tasks.extract import TaskExtractor
from domain.tasks.service import TaskEdit, TaskService
from fixtures.app_factory import FakeBackend, app_client, make_container
from fixtures.synthetic import synthetic_rrn
from integrations.gknowledge.browser import (
    BrowserKnowledgeStore,
    RegisterFields,
    SearchFields,
    load_fields,
)
from integrations.gknowledge.fake import FakeKnowledgeStore
from security.browser import BrowserRuntime
from security.logging_policy import Site
from security.masking import mask
from security.sessions import SessionStore
from storage.db import Database
from storage.knowledge_repos import SqliteHistoryRepository, SqliteLedgerRepository
from storage.repositories import (
    SqliteEventRecorder,
    SqliteMailRefRepository,
    SqliteTaskRepository,
)


def _setup(db: Database, clock: FixedClock, store: Any) -> tuple[KnowledgeService, int]:
    tasks = TaskService(
        SqliteTaskRepository(db),
        SqliteMailRefRepository(db),
        SqliteEventRecorder(db, clock),
        SqliteLedgerRepository(db),
        clock,
        TaskExtractor(None),
        "김가상",
    )
    task = tasks.add_manual("전표 역분개 오류", TaskEdit(requester_dept="재무팀", module_tag="FI"))
    assert task.id is not None
    tasks.change_status(task.id, TaskStatus.DONE)
    SqliteHistoryRepository(db).add(
        HistoryCard(
            task.id, "역분개 오류", "전기기간 닫힘", "OB52 오픈 후 재처리", clock.now(), "OB52"
        )
    )
    return KnowledgeService(store, tasks, SqliteHistoryRepository(db), "김가상"), task.id


def test_gk02_ret03_draft_contains_rnr(db: Database, clock: FixedClock) -> None:
    service, task_id = _setup(db, clock, FakeKnowledgeStore())
    draft = service.preview(task_id)
    body = draft.body.value
    for item in (
        "업무명: 전표 역분개 오류",
        "담당자: 김가상",
        "요청 부서: 재무팀",
        "처리일:",
        "FI",
        "OB52",
    ):
        assert item in body
    assert [t.value for t in draft.tags][0] == "FI"


def test_gk07_draft_masks_sensitive_values(db: Database, clock: FixedClock) -> None:
    rrn = synthetic_rrn()
    draft = draft_from_edit("문의 010-1234-5678", f"주민번호 {rrn} 확인", ["태그"])
    assert rrn not in draft.body.value
    assert "010-1234-5678" not in draft.title.value
    with pytest.raises(ValueError):
        draft_from_edit(" ", "본문", [])


def test_gk06_register_links_and_blocks_duplicate(db: Database, clock: FixedClock) -> None:
    store = FakeKnowledgeStore()
    service, task_id = _setup(db, clock, store)
    draft = service.preview(task_id)
    task = service.register(task_id, draft)
    assert task.gk_link and task.gk_link.startswith("https://gk.example.invalid/")
    with pytest.raises(DuplicateRegistrationError):
        service.register(task_id, draft)
    service.register(task_id, draft, force=True)  # 재등록 버튼
    assert len(store.items) == 2
    [entry] = SqliteLedgerRepository(db).list_entries()
    assert entry.gk_registered


def test_register_failure_keeps_task_unregistered(db: Database, clock: FixedClock) -> None:
    service, task_id = _setup(db, clock, FakeKnowledgeStore(fail=True))
    with pytest.raises(KnowledgeUnavailableError):
        service.register(task_id, service.preview(task_id))
    assert service.preview(task_id) is not None
    assert not SqliteLedgerRepository(db).list_entries()[0].gk_registered


def test_search_finds_registered(db: Database, clock: FixedClock) -> None:
    store = FakeKnowledgeStore()
    service, task_id = _setup(db, clock, store)
    service.register(task_id, service.preview(task_id))
    assert service.search(mask("역분개 오류"))
    assert service.search(mask("무관한 키워드")) == []


def test_default_field_map_is_unconfirmed() -> None:
    register, search = load_fields()
    assert register.path == "/knowledge/new"
    assert not register.ready and not search.ready


def test_field_map_validation(tmp_path: Path) -> None:
    bad = tmp_path / "f.toml"
    bad.write_text("[register]\nunknown = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_fields(bad)


class FakePage:
    def __init__(self, log: list[tuple[str, ...]], result: str) -> None:
        self.log = log
        self.url = result

    def goto(self, url: str, timeout: int) -> None:
        self.log.append(("goto", url))

    def fill(self, selector: str, value: str) -> None:
        self.log.append(("fill", selector, value))

    def click(self, selector: str) -> None:
        self.log.append(("click", selector))

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        return None

    def get_attribute(self, selector: str, name: str) -> str:
        return self.url

    def close(self) -> None:
        self.log.append(("close",))


def _browser_store(
    clock: FixedClock, result: str
) -> tuple[BrowserKnowledgeStore, list[Any], BrowserRuntime]:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    sessions = SessionStore(clock, timedelta(minutes=30))
    context = runtime.open_context()
    log: list[tuple[str, ...]] = []
    context._raw.new_page = lambda: FakePage(log, result)  # type: ignore[union-attr]
    sessions.open(Site.GKNOWLEDGE, context)
    fields = RegisterFields("/knowledge/new", "#t", "#b", "", "#s", "")
    empty = SearchFields("", "", "", "", "", "")
    return (
        BrowserKnowledgeStore("https://gk.example.invalid", sessions, fields, empty),
        log,
        runtime,
    )


def test_gk03_browser_fill_and_submit(clock: FixedClock) -> None:
    store, log, runtime = _browser_store(clock, "https://gk.example.invalid/knowledge/77")
    try:
        result = store.register(draft_from_edit("제목", "본문", []))
        assert result.link.endswith("/knowledge/77")
        assert ("goto", "https://gk.example.invalid/knowledge/new") in log
        assert ("click", "#s") in log and log[-1] == ("close",)
        with pytest.raises(KnowledgeUnavailableError, match="확인 필요"):
            store.search(mask("검색"))
    finally:
        runtime.shutdown()


def test_foreign_result_link_rejected(clock: FixedClock) -> None:
    store, _log, runtime = _browser_store(clock, "https://evil.example/phish")
    try:
        with pytest.raises(KnowledgeUnavailableError):
            store.register(draft_from_edit("제목", "본문", []))
    finally:
        runtime.shutdown()


def test_gk05_requires_login(clock: FixedClock) -> None:
    sessions = SessionStore(clock, timedelta(minutes=30))
    fields = RegisterFields("/n", "#t", "#b", "", "#s", "")
    store = BrowserKnowledgeStore(
        "https://gk.example.invalid", sessions, fields, SearchFields("", "", "", "", "", "")
    )
    with pytest.raises(KnowledgeUnavailableError, match="로그인"):
        store.register(draft_from_edit("제목", "본문", []))


def test_gk_api_preview_register(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        task = client.post("/api/tasks", json={"title": "출하 I/F 재처리"}).json()
        preview = client.get(f"/api/gk/preview/{task['id']}").json()
        assert "R&R" in preview["body"]
        payload = {"title": preview["title"], "body": preview["body"], "tags": preview["tags"]}
        registered = client.post(f"/api/gk/register/{task['id']}", json=payload)
        assert registered.status_code == 200 and registered.json()["gk_link"]
        again = client.post(f"/api/gk/register/{task['id']}", json=payload)
        assert again.status_code == 409
