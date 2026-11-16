"""주간보고 미리보기 편집기 테스트(WIKI-02·03·04·06, 결정기록 0030). 가짜 위키·합성 값만 쓴다."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from domain.calendar import week_start
from domain.clock import KST, FixedClock
from domain.errors import UserInputError
from domain.tasks.extract import TaskExtractor
from domain.tasks.service import TaskService
from domain.weekly.convert import to_platform_text
from domain.weekly.drafts import WeeklyDraftStore
from domain.weekly.links import Platform
from domain.weekly.report import WeeklyDraft, WikiTemplate
from domain.weekly.rich import (
    MAX_HTML_BYTES,
    RichHtmlError,
    blocks,
    sanitize,
    unmasked_text_left,
)
from domain.weekly.service import WeeklyService
from fixtures.app_factory import app_client, make_container
from integrations.wiki.fake import FakeWiki
from storage.db import Database
from storage.knowledge_repos import SqliteLedgerRepository, SqlitePublishLog
from storage.repositories import (
    SqliteEventRecorder,
    SqliteMailRefRepository,
    SqliteTaskRepository,
)
from storage.retention import RetentionJob
from storage.weekly_repos import SqliteWeeklyDrafts

WIKI = "https://wiki.example.invalid/"
TEMPLATE = WikiTemplate(space_key="ERP")
PHONE = "010-1234-5678"
ACCOUNT = "110-123-456789"


# ---- 정리기 ----
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<p>a<script>alert(1)</script>b</p>", "<p>ab</p>"),
        ('<p onclick="x()">글자</p>', "<p>글자</p>"),
        ('<img src=x onerror="alert(1)">남김', "남김"),
        ('<a href="javascript:alert(1)">링크</a>', "<a>링크</a>"),
        ('<a href="https://evil.example/x">밖</a>', "<a>밖</a>"),
        (f'<a href="{WIKI}pages/1">위키</a>', f'<a href="{WIKI}pages/1">위키</a>'),
        ('<p style="color:red">색</p><style>p{}</style>', "<p>색</p>"),
        ("<!-- 주석 --><p>본문</p>", "<p>본문</p>"),
        ("<iframe src='https://x'>숨김</iframe><p>ok</p>", "<p>ok</p>"),
        ("<h2>둘째</h2>", "<h3>둘째</h3>"),  # 본인 영역 경계(h2) 보호
        ("<h1>첫째</h1>", "첫째"),
        ("<ul><li>하나<li>둘</ul>", "<ul><li>하나<li>둘</li></li></ul>"),
        ("<p><b>굵게</p>", "<p><b>굵게</b></p>"),  # 깨진 HTML은 닫아 준다
        ("</div></p>글자", "글자"),
        ('<td colspan="3" rowspan="99" class="x">칸</td>', '<td colspan="3">칸</td>'),
        ("<p>a &lt;b&gt; &amp; c</p>", "<p>a &lt;b&gt; &amp; c</p>"),
        ("<br><br/>", "<br /><br />"),
    ],
)
def test_wiki02_sanitizer_allow_list(raw: str, expected: str) -> None:
    assert sanitize(raw, (WIKI,)) == expected


def test_wiki02_nested_table_tags_are_dropped() -> None:
    raw = "<table><tr><td>밖<table><tr><td>안</td></tr></table></td></tr></table>"
    assert sanitize(raw) == "<table><tr><td>밖안</td></tr></table>"


def test_wiki02_size_limit() -> None:
    with pytest.raises(RichHtmlError):
        sanitize("<p>" + "가" * (MAX_HTML_BYTES // 3 + 10) + "</p>")


def test_wiki02_masks_text_nodes_and_detects_split_values() -> None:
    clean = sanitize(f"<p>연락처 {PHONE}</p><td>계좌 {ACCOUNT}</td>")
    assert PHONE not in clean and ACCOUNT not in clean
    split = sanitize("<p>010-1234-<b>5678</b></p>")
    assert unmasked_text_left(split)  # 서식 사이에 걸친 값은 게시 전에 거부 대상
    assert sanitize(clean) == clean  # 같은 결과(재검사 안정성)


def test_wiki05_html_blocks_to_platform_text() -> None:
    html = (
        "<h3>금주 실적</h3><ul><li>항목 *하나*</li></ul>"
        "<table><tr><td>a</td><td>b</td></tr></table>"
    )
    assert blocks(html) == [("heading", "금주 실적"), ("item", "항목 *하나*"), ("row", "a | b")]
    draft = WeeklyDraft(week_start(datetime(2026, 11, 16, 10, 0, tzinfo=KST)), html=html)
    wiki_text = to_platform_text(draft, TEMPLATE, "김가상", Platform.WIKI)
    assert "h3. 금주 실적" in wiki_text and "* 항목 \\*하나\\*" in wiki_text
    assert "a \\| b" in wiki_text  # 위키 표기 문자(|)는 이스케이프


# ---- 서비스: 미리보기·승인·게시 ----
def _weekly(db: Database, clock: FixedClock, wiki: FakeWiki) -> WeeklyService:
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
    return WeeklyService(
        tasks, wiki, events, SqlitePublishLog(db), clock, TEMPLATE, "김가상", wiki_base_url=WIKI
    )


def _rich(clock: FixedClock, html: str) -> WeeklyDraft:
    return WeeklyDraft(week_start(clock.now()), html=html)


def test_wiki03_rich_preview_masks_and_publish_matches(db: Database, clock: FixedClock) -> None:
    wiki = FakeWiki()
    weekly = _weekly(db, clock, wiki)
    draft = _rich(clock, f"<h3>금주 실적</h3><table><tr><td>연락 {PHONE}</td></tr></table>")
    preview = weekly.preview(draft)
    assert PHONE not in preview.section_html and "<table>" in preview.section_html
    page = weekly.publish(weekly.approve(draft, preview.nonce, "create"))
    assert PHONE not in page.body and page.body == preview.section_html


def test_wiki03_rich_publish_requires_same_content(db: Database, clock: FixedClock) -> None:
    weekly = _weekly(db, clock, FakeWiki())
    draft = _rich(clock, "<p>원래 내용</p>")
    preview = weekly.preview(draft)
    with pytest.raises(UserInputError):
        weekly.approve(_rich(clock, "<p>바뀐 내용</p>"), preview.nonce, "create")
    with pytest.raises(UserInputError):
        weekly.approve(draft, "없는-번호-0000000000", "create")


def test_wiki03_split_sensitive_value_is_rejected(db: Database, clock: FixedClock) -> None:
    weekly = _weekly(db, clock, FakeWiki())
    with pytest.raises(UserInputError):
        weekly.preview(_rich(clock, "<p>010-1234-<b>5678</b></p>"))


def test_wiki04_rich_updates_only_own_section(db: Database, clock: FixedClock) -> None:
    wiki = FakeWiki()
    weekly = _weekly(db, clock, wiki)
    title = TEMPLATE.page_title(week_start(clock.now()))
    before = "<h2>박샘플</h2><ul><li>다른 사람 &lt;유지&gt;</li></ul>"
    after = "<h2>최테스트</h2><p>타인</p>"
    wiki.create_page("ERP", title, before + "<h2>김가상</h2><p>예전</p>" + after)
    # 사용자가 h2를 넣어도 h3으로 낮춰 다른 영역을 만들거나 덮지 못한다
    draft = _rich(clock, "<h2>최테스트</h2><ul><li>새 내용</li></ul>")
    preview = weekly.preview(draft)
    page = weekly.publish(weekly.approve(draft, preview.nonce, "update"))
    assert page.body.startswith(before) and page.body.endswith(after)
    assert "<h3>최테스트</h3>" in page.body and "예전" not in page.body


# ---- API ----
def test_wiki02_api_draft_html_preview_publish(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/tasks", json={"title": "진행 업무"})
        draft = client.post("/api/weekly/draft", json={}).json()
        assert "<h3>" in draft["draft_html"] and draft["page_title"] and draft["own_heading"]
        html = draft["draft_html"] + f"<table><tr><td>{PHONE}</td></tr></table><script>x</script>"
        body = {"week_start": draft["week_start"], "form": draft["form"], "html": html}
        preview = client.post("/api/weekly/preview", json=body).json()
        assert PHONE not in preview["section_html"] and "script" not in preview["section_html"]
        published = client.post(
            "/api/weekly/publish",
            json={**body, "nonce": preview["nonce"], "mode": "create", "approved": True},
        )
        assert published.status_code == 200
        cache = client.get("/api/weekly/cache").json()
        assert cache and PHONE not in str(cache)
        too_big = {**body, "html": "<p>" + "a" * (MAX_HTML_BYTES + 1) + "</p>"}
        assert client.post("/api/weekly/preview", json=too_big).status_code in (400, 422)
        extra = {**body, "unknown": 1}
        assert client.post("/api/weekly/preview", json=extra).status_code == 422  # extra=forbid


def test_wiki06_autosave_and_temp_save(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    start = week_start(clock.now()).isoformat()
    with app_client(container) as client:
        empty = client.get("/api/weekly/saved-draft", params={"week_start": start}).json()
        assert empty["html"] is None
        auto = client.post(
            "/api/weekly/autosave", json={"week_start": start, "html": f"<p>{PHONE}</p>"}
        )
        assert auto.status_code == 200
        loaded = client.get("/api/weekly/saved-draft", params={"week_start": start}).json()
        assert loaded["stored"] is False and PHONE not in loaded["html"]
        saved = client.post(
            "/api/weekly/save-draft", json={"week_start": start, "html": "<p>저장</p>"}
        )
        assert saved.status_code == 200 and saved.json()["stored"] is True
        bad = client.post(
            "/api/weekly/save-draft", json={"week_start": "2026-11-17T00:00:00+09:00", "html": "x"}
        )
        assert bad.status_code == 400
    rows = container.db.query("SELECT snapshot FROM weekly_draft")
    assert [r["snapshot"] for r in rows] == ["<p>저장</p>"]


def test_ret_weekly_draft_purged_after_retention(tmp_path: Path, clock: FixedClock) -> None:
    db = Database(tmp_path / "r.db")
    store = WeeklyDraftStore(SqliteWeeklyDrafts(db), clock)
    store.save(week_start(clock.now()), "<p>임시</p>")
    clock.advance(timedelta(days=8))
    RetentionJob(db, clock, retention_days=7, keep_ledger=False).run()
    assert db.query("SELECT COUNT(*) FROM weekly_draft")[0][0] == 0
