"""위키 주간보고 테스트(WIKI-01~06). 가짜 위키만 쓴다."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from domain.calendar import week_range
from domain.clock import KST, FixedClock
from domain.models import TaskStatus
from domain.tasks.extract import TaskExtractor
from domain.tasks.service import TaskEdit, TaskService
from domain.weekly.report import (
    OwnSectionError,
    WeeklyDraft,
    WikiTemplate,
    classify_week,
    replace_own_section,
)
from domain.weekly.service import (
    ApprovedReport,
    WeeklyService,
    WikiConflictError,
    WikiUnavailableError,
)
from fixtures.app_factory import app_client, make_container
from integrations.wiki.fake import FakeWiki
from storage.db import Database
from storage.knowledge_repos import SqliteLedgerRepository, SqlitePublishLog
from storage.repositories import (
    SqliteEventRecorder,
    SqliteMailRefRepository,
    SqliteTaskRepository,
)

TEMPLATE = WikiTemplate(space_key="ERP")


def _services(db: Database, clock: FixedClock, wiki: FakeWiki) -> tuple[TaskService, WeeklyService]:
    events = SqliteEventRecorder(db, clock)
    tasks = TaskService(
        SqliteTaskRepository(db),
        SqliteMailRefRepository(db),
        events,
        SqliteLedgerRepository(db),
        clock,
        TaskExtractor(None),
        "김가상",
    )
    weekly = WeeklyService(tasks, wiki, events, SqlitePublishLog(db), clock, TEMPLATE, "김가상")
    return tasks, weekly


def test_wiki01_week_boundaries_and_classification(db: Database, clock: FixedClock) -> None:
    tasks, _ = _services(db, clock, FakeWiki())
    start, end = week_range(clock.now())
    inside = tasks.add_manual("금주 완료", TaskEdit(module_tag="FI"))
    before = tasks.add_manual("지난주 완료")
    planned = tasks.add_manual("진행 업무")
    hold = tasks.add_manual("보류 업무")
    assert inside.id and before.id and planned.id and hold.id
    clock._current = start  # 목 00:00 정각 완료 → 금주 포함
    tasks.change_status(inside.id, TaskStatus.DONE)
    clock._current = start - timedelta(seconds=1)  # 수 23:59:59 → 지난주
    tasks.change_status(before.id, TaskStatus.DONE)
    tasks.change_status(hold.id, TaskStatus.ON_HOLD)
    draft = classify_week(tasks.list_tasks(), start + timedelta(days=1), TEMPLATE)
    assert draft.done == ["[FI] 금주 완료 (요청: -)"]
    assert draft.planned == ["[기타] 진행 업무 (요청: -)"]
    assert draft.issues == ["[기타] 보류 업무 (요청: -)"]
    assert draft.week_start == start and end - start == timedelta(days=7)


def test_wiki02_page_title_format_thursday_default() -> None:
    start = datetime(2026, 11, 12, tzinfo=KST)  # 목
    assert TEMPLATE.page_title(start) == "ERP_261112"  # 사용자 결정 2026-11-12: 목요일


def _draft(clock: FixedClock) -> WeeklyDraft:
    start, _ = week_range(clock.now())
    return WeeklyDraft(start, ["[FI] 전표 오류 조치 010-1234-5678"], ["[MM] PO 양식"], [])


def test_wiki03_publish_requires_approval_object(db: Database, clock: FixedClock) -> None:
    _, weekly = _services(db, clock, FakeWiki())
    with pytest.raises(TypeError):
        weekly.publish(_draft(clock))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ApprovedReport(_draft(clock), None, "create", clock.now())  # type: ignore[arg-type]


def test_approval_rejects_changed_content(db: Database, clock: FixedClock) -> None:
    _, weekly = _services(db, clock, FakeWiki())
    draft = _draft(clock)
    preview = weekly.preview(draft)
    draft.done.append("미리보기 이후 추가")
    with pytest.raises(ValueError):
        weekly.approve(draft, preview.nonce, "create")


def test_create_masks_and_records(db: Database, clock: FixedClock) -> None:
    wiki = FakeWiki()
    _, weekly = _services(db, clock, wiki)
    draft = _draft(clock)
    preview = weekly.preview(draft)
    page = weekly.publish(weekly.approve(draft, preview.nonce, "create"))
    assert "010-1234-5678" not in page.body
    assert page.title == TEMPLATE.page_title(draft.week_start)
    [record] = SqlitePublishLog(db).list_recent()
    assert record.page_version == 1 and record.page_link  # WIKI-06


def test_wiki04_only_own_section_changes(db: Database, clock: FixedClock) -> None:
    wiki = FakeWiki()
    _, weekly = _services(db, clock, wiki)
    title = TEMPLATE.page_title(_draft(clock).week_start)
    other = "<h2>박샘플</h2><ul><li>다른 사람 내용 &lt;유지&gt;</li></ul>"
    wiki.create_page(
        "ERP", title, other + "<h2>김가상</h2><p>예전 내용</p><h2>최테스트</h2><p>타인</p>"
    )
    draft = _draft(clock)
    preview = weekly.preview(draft)
    page = weekly.publish(weekly.approve(draft, preview.nonce, "update"))
    assert page.body.startswith(other)
    assert page.body.endswith("<h2>최테스트</h2><p>타인</p>")
    assert "예전 내용" not in page.body and "PO 양식" in page.body


def test_version_conflict_stops_publish(db: Database, clock: FixedClock) -> None:
    wiki = FakeWiki()
    _, weekly = _services(db, clock, wiki)
    title = TEMPLATE.page_title(_draft(clock).week_start)
    wiki.create_page("ERP", title, "<h2>김가상</h2><p>x</p>")
    draft = _draft(clock)
    preview = weekly.preview(draft)
    wiki.edit_by_other(title, "<h2>김가상</h2><p>x</p><h2>박샘플</h2><p>새 글</p>")
    approved = weekly.approve(draft, preview.nonce, "update")
    with pytest.raises(WikiConflictError):
        weekly.publish(approved)
    assert "새 글" in wiki.pages[title].body


def test_duplicate_heading_refused() -> None:
    body = "<h2>김가상</h2><p>a</p><h2>김가상</h2><p>b</p>"
    with pytest.raises(OwnSectionError):
        replace_own_section(body, "김가상", "<h2>김가상</h2>")
    assert replace_own_section("<h2>타인</h2>", "김가상", "<h2>김가상</h2>").endswith(
        "<h2>김가상</h2>"
    )


def test_preview_escapes_html(db: Database, clock: FixedClock) -> None:
    _, weekly = _services(db, clock, FakeWiki())
    draft = WeeklyDraft(_draft(clock).week_start, ["<script>alert(1)</script>"], [], [])
    assert "<script>" not in weekly.preview(draft).section_html


def test_unconfirmed_space_cannot_publish(db: Database, clock: FixedClock) -> None:
    wiki = FakeWiki()
    tasks, _ = _services(db, clock, wiki)
    weekly = WeeklyService(
        tasks,
        wiki,
        SqliteEventRecorder(db, clock),
        SqlitePublishLog(db),
        clock,
        replace(TEMPLATE, space_key=""),
        "김가상",
    )
    draft = _draft(clock)
    preview = weekly.preview(draft)
    with pytest.raises(WikiUnavailableError):
        weekly.publish(weekly.approve(draft, preview.nonce, "create"))


def test_weekly_api_flow(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/tasks", json={"title": "진행 업무"})
        draft = client.post("/api/weekly/draft", json={}).json()
        assert draft["planned"]
        preview = client.post("/api/weekly/preview", json=draft).json()
        body = {**draft, "nonce": preview["nonce"], "mode": "create"}
        assert client.post("/api/weekly/publish", json=body).status_code == 422  # 승인 없음
        published = client.post("/api/weekly/publish", json={**body, "approved": True})
        assert published.status_code == 200
        assert client.get("/api/weekly/history").json()[0]["version"] == 1
        bad_week = {**draft, "week_start": "2026-11-13T00:00:00+09:00"}
        assert client.post("/api/weekly/preview", json=bad_week).status_code == 400


# ---- 05~12단계 보안 검토 회귀(H-1·H-2·L-1·L-5) ----


def test_h1_section_stops_at_h1_and_layout_boundary() -> None:
    body = "<h2>김가상</h2><p>A</p><h1>공통 공지</h1><p>팀장 작성</p>"
    result = replace_own_section(body, "김가상", "<h2>김가상</h2><p>새</p>")
    assert result == "<h2>김가상</h2><p>새</p><h1>공통 공지</h1><p>팀장 작성</p>"
    layout = (
        "<ac:layout><ac:layout-section><ac:layout-cell><h2>김가상</h2><p>A</p></ac:layout-cell>"
        "<ac:layout-cell><h2>박샘플</h2><p>B</p></ac:layout-cell></ac:layout-section></ac:layout>"
    )
    result = replace_own_section(layout, "김가상", "<h2>김가상</h2><p>새</p>")
    assert "<h2>박샘플</h2><p>B</p></ac:layout-cell>" in result
    assert result.count("<ac:layout-cell>") == 2 and "<p>A</p>" not in result


def test_h1_heading_with_inner_tags_found() -> None:
    body = "<h2><strong>김가상</strong></h2><p>old</p><h2>박샘플</h2>"
    assert (
        replace_own_section(body, "김가상", "<h2>김가상</h2>") == "<h2>김가상</h2><h2>박샘플</h2>"
    )


def test_l5_unclosed_heading_is_linear() -> None:
    import time

    started = time.perf_counter()
    replace_own_section("<h2" + " a" * 50_000, "김가상", "<h2>김가상</h2>")
    assert time.perf_counter() - started < 1.0


def test_h2_default_author_cannot_publish(db: Database, clock: FixedClock) -> None:
    wiki = FakeWiki()
    tasks, _ = _services(db, clock, wiki)
    weekly = WeeklyService(
        tasks, wiki, SqliteEventRecorder(db, clock), SqlitePublishLog(db), clock, TEMPLATE, "본인"
    )
    draft = _draft(clock)
    preview = weekly.preview(draft)
    with pytest.raises(WikiUnavailableError, match="고유한 이름"):
        weekly.publish(weekly.approve(draft, preview.nonce, "create"))


def test_l1_nonce_is_single_use_and_server_bound(db: Database, clock: FixedClock) -> None:
    from domain.errors import UserInputError

    _, weekly = _services(db, clock, FakeWiki())
    draft = _draft(clock)
    with pytest.raises(UserInputError):
        weekly.approve(draft, "x" * 32, "create")  # 미리보기 없이 승인 불가
    preview = weekly.preview(draft)
    weekly.publish(weekly.approve(draft, preview.nonce, "create"))
    with pytest.raises(UserInputError):
        weekly.approve(draft, preview.nonce, "create")  # 재사용 불가


def test_preview_shows_current_own_section(db: Database, clock: FixedClock) -> None:
    wiki = FakeWiki()
    _, weekly = _services(db, clock, wiki)
    title = TEMPLATE.page_title(_draft(clock).week_start)
    wiki.create_page("ERP", title, "<h2>김가상</h2><p>지난주 내용</p>")
    assert weekly.preview(_draft(clock)).current_section == "김가상지난주 내용"
