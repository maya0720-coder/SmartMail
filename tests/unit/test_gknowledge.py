"""G-Knowledge 등록·검색 테스트(GK-01~07, RET-03). 가짜 저장소·가짜 브라우저만 쓴다."""

from __future__ import annotations

import time
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
    with pytest.raises(DuplicateRegistrationError, match="중복 등록 불가"):
        service.register(task_id, draft)  # 재등록 경로 없음(결정기록 0008 G-6)
    assert len(store.items) == 1
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
        self.items: list[Any] = []

    def goto(self, url: str, timeout: int) -> None:
        self.log.append(("goto", url))

    def fill(self, selector: str, value: str) -> None:
        self.log.append(("fill", selector, value))

    def click(self, selector: str) -> None:
        self.log.append(("click", selector))

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        return None

    def wait_for_url(self, predicate: Any, timeout: int) -> None:
        if not predicate(self.url):
            raise TimeoutError("주소가 바뀌지 않음")

    def wait_for_selector(self, selector: str, timeout: int) -> None:
        if not self.items and selector == ".item":
            raise TimeoutError("결과 없음")

    def query_selector_all(self, selector: str) -> list[Any]:
        return self.items

    def get_attribute(self, selector: str, name: str) -> str:
        return self.url

    def close(self) -> None:
        self.log.append(("close",))


def _browser_store(
    clock: FixedClock, result: str, now: Any = time.monotonic, page_cls: Any = None
) -> tuple[BrowserKnowledgeStore, list[Any], BrowserRuntime]:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    sessions = SessionStore(clock, timedelta(minutes=30))
    context = runtime.open_context()
    log: list[tuple[str, ...]] = []
    cls = page_cls or FakePage
    context._raw.new_page = lambda: cls(log, result)  # type: ignore[union-attr]
    sessions.open(Site.GKNOWLEDGE, context)
    fields = RegisterFields("/knowledge/new", "#t", "#b", "", "#s", "", "/knowledge/[0-9]+")
    empty = SearchFields("", "", "", "", "", "")
    return (
        BrowserKnowledgeStore("https://gk.example.invalid", sessions, fields, empty, now=now),
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
    fields = RegisterFields("/n", "#t", "#b", "", "#s", "", "/k/[0-9]+")
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


def test_gk06_same_task_reentry_and_same_title_rejected(db: Database, clock: FixedClock) -> None:
    store = FakeKnowledgeStore()
    service, task_id = _setup(db, clock, store)
    draft = service.preview(task_id)
    original = store.register

    def reentrant(d: Any) -> Any:
        with pytest.raises(DuplicateRegistrationError, match="등록하는 중"):
            service.register(task_id, d)  # 같은 업무 동시 등록(두 번 클릭)
        return original(d)

    store.register = reentrant  # type: ignore[method-assign,assignment]
    service.register(task_id, draft)
    other = service._tasks.add_manual("전표 역분개 오류", TaskEdit())
    assert other.id is not None
    with pytest.raises(DuplicateRegistrationError, match="같은 제목"):
        service.register(other.id, service.preview(other.id))
    assert len(store.items) == 1


def test_gk03_tags_appended_to_body_without_tag_field(clock: FixedClock) -> None:
    store, log, runtime = _browser_store(clock, "https://gk.example.invalid/knowledge/9")
    try:
        store.register(draft_from_edit("제목", "본문", ["FI", "역분개"]))
        fills = {entry[1]: entry[2] for entry in log if entry[0] == "fill"}
        assert fills["#b"].endswith("태그: FI, 역분개")
    finally:
        runtime.shutdown()


def test_gk04_result_not_confirmed_when_still_on_register_page(clock: FixedClock) -> None:
    store, _log, runtime = _browser_store(clock, "https://gk.example.invalid/knowledge/new")
    try:
        with pytest.raises(KnowledgeUnavailableError, match="등록 여부를 먼저 확인"):
            store.register(draft_from_edit("제목", "본문", []))
    finally:
        runtime.shutdown()


class FakeItem:
    def __init__(self, title: str, href: str) -> None:
        self._title = title
        self._href = href

    def query_selector(self, selector: str) -> FakeItem:
        return self

    def inner_text(self) -> str:
        return self._title

    def get_attribute(self, name: str) -> str:
        return self._href


def test_srch02_search_by_url_query(clock: FixedClock) -> None:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    sessions = SessionStore(clock, timedelta(minutes=30))
    context = runtime.open_context()
    log: list[tuple[str, ...]] = []
    page = FakePage(log, "")
    page.items = [FakeItem("역분개 처리", "/k/1"), FakeItem("외부", "https://evil.example/x")]
    context._raw.new_page = lambda: page  # type: ignore[union-attr]
    sessions.open(Site.GKNOWLEDGE, context)
    search = SearchFields("/search?q={query}", "", "", ".item", "", "a")
    register = RegisterFields("/knowledge/new", "", "", "", "", "", "")
    store = BrowserKnowledgeStore("https://gk.example.invalid", sessions, register, search)
    try:
        assert search.ready
        hits = store.search(mask("역분개 오류"))
        assert [h.link for h in hits] == ["https://gk.example.invalid/k/1"]
        assert log[0][0] == "goto" and "q=%EC%97%AD" in log[0][1]
        assert not [entry for entry in log if entry[0] == "fill"]
        page.items = []
        assert store.search(mask("없는 내용")) == []
    finally:
        runtime.shutdown()


@pytest.mark.parametrize(
    "result",
    [
        "https://gk.example.invalid/login",  # SSO 재인증 화면
        "https://gk.example.invalid/",  # 권한 없음 등으로 첫 화면 이동
        "http://gk.example.invalid/knowledge/7",  # 평문
    ],
)
def test_gk04_non_result_page_is_not_success(clock: FixedClock, result: str) -> None:
    store, _log, runtime = _browser_store(clock, result)
    try:
        with pytest.raises(KnowledgeUnavailableError, match="등록 여부를 먼저 확인"):
            store.register(draft_from_edit("제목", "본문", []))
    finally:
        runtime.shutdown()


def test_gk06_not_sent_when_dequeued_after_deadline(clock: FixedClock) -> None:
    times = iter([0.0, 10_000.0])
    store, log, runtime = _browser_store(
        clock, "https://gk.example.invalid/knowledge/7", now=lambda: next(times)
    )
    try:
        with pytest.raises(KnowledgeUnavailableError, match="등록하지 않았습니다"):
            store.register(draft_from_edit("제목", "본문", []))
        assert not [entry for entry in log if entry[0] in ("fill", "click")]
    finally:
        runtime.shutdown()


class BrokenClickPage(FakePage):
    def click(self, selector: str) -> None:
        raise RuntimeError("클릭 후 화면 이동 대기 시간 초과")


def test_gk06_click_failure_is_uncertain(clock: FixedClock) -> None:
    store, _log, runtime = _browser_store(
        clock, "https://gk.example.invalid/knowledge/7", page_cls=BrokenClickPage
    )
    try:
        with pytest.raises(KnowledgeUnavailableError, match="등록 여부를 먼저 확인"):
            store.register(draft_from_edit("제목", "본문", []))
    finally:
        runtime.shutdown()


def test_register_fields_need_result_check(tmp_path: Path) -> None:
    assert not RegisterFields("/n", "#t", "#b", "", "#s", "", "").ready
    assert RegisterFields("/n", "#t", "#b", "", "#s", "#done", "").ready
    bad = tmp_path / "f.toml"
    bad.write_text("[register]\nresult_path = '['\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_fields(bad)


def test_srch02_search_error_is_not_empty_result(clock: FixedClock) -> None:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    sessions = SessionStore(clock, timedelta(minutes=30))
    context = runtime.open_context()
    page = FakePage([], "")

    def broken_wait(selector: str, timeout: int) -> None:
        raise RuntimeError("context closed")

    page.wait_for_selector = broken_wait  # type: ignore[method-assign]
    context._raw.new_page = lambda: page  # type: ignore[union-attr]
    sessions.open(Site.GKNOWLEDGE, context)
    search = SearchFields("/search?q={query}", "", "", ".item", "", "a")
    register = RegisterFields("/knowledge/new", "", "", "", "", "", "")
    store = BrowserKnowledgeStore("https://gk.example.invalid", sessions, register, search)
    try:
        with pytest.raises(KnowledgeUnavailableError, match="검색에 실패"):
            store.search(mask("역분개"))
    finally:
        runtime.shutdown()
