"""주간보고 HTML 편집 정리기·복사 형식 7종·스키마 8 테스트(C1·C2, WIKI-02·05·06, 0041).

합성 값만 쓴다. 실제 위키·Slack·Teams·메일에 접속하지 않는다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from domain.calendar import week_start
from domain.clock import KST
from domain.errors import UserInputError
from domain.weekly.convert_formats import (
    export_draft,
    to_mail_html,
    to_markdown,
    to_slack_html,
    to_storage_xhtml,
    to_teams_html,
)
from domain.weekly.links import Platform, detect_platform
from domain.weekly.report import WeeklyDraft, WikiTemplate
from domain.weekly.rich import (
    MAX_HTML_BYTES,
    CleanHtml,
    RichHtmlError,
    clean_html,
    sanitize,
    sanitize_report,
)
from domain.weekly.targets import DEFAULT_FORMAT, FORMATS, ExportFormat
from integrations.publish.fake import FakePublishTarget
from storage.db import SCHEMA, SCHEMA_VERSION, Database

PHONE = "010-1234-5678"
TEMPLATE = WikiTemplate(space_key="ERP")
AUTHOR = "김가상"
BODY = (
    "<h3>금주 실적</h3><ul><li>항목 a|b &lt;@U123&gt; @here</li></ul>"
    "<table><tr><th>구분</th><th>내용</th></tr>"
    f"<tr><td>x|y</td><td>*z* 연락 {PHONE}</td></tr></table>"
)


# ---- C1: 정리기 허용 목록 확장(D1) ----
@pytest.mark.parametrize(
    "raw",
    [
        "<hr /><blockquote>인용</blockquote><pre><code>코드</code></pre>",
        "<p><s>취소</s><sub>1</sub><sup>2</sup><span>묶음</span></p><div>블록</div>",
        '<table><caption>표 제목</caption><colgroup><col span="2" width="50%" /></colgroup>'
        '<tfoot><tr><td align="center" valign="top" width="120">합계</td></tr></tfoot></table>',
        '<ol start="3"><li>셋째</li></ol>',
        '<p style="color: red; text-align: center">색</p>',
    ],
)
def test_wiki02_html_edit_tags_pass(raw: str) -> None:
    clean, removed = sanitize_report(raw, ())
    assert clean == raw and removed == {}


@pytest.mark.parametrize(
    "raw",
    [
        '<p style="background: url(https://x.invalid/a.png)">a</p>',
        '<p style="width: expression(alert(1))">a</p>',
        '<p style="color: red; position: absolute">a</p>',
        '<p style="color: \\72 ed">a</p>',
        '<p style="color: red /* x */">a</p>',
    ],
)
def test_wiki02_forbidden_style_values_removed(raw: str) -> None:
    clean, removed = sanitize_report(raw, ())
    for bad in ("url(", "expression", "position", "\\", "/*"):
        assert bad not in clean
    assert removed  # 버린 내용은 요약된다


def test_wiki02_dangerous_markup_removed_with_counts() -> None:
    raw = (
        '<img src="x" onerror="alert(1)"><iframe src="https://x.invalid">숨김</iframe>'
        '<p onclick="a()" onmouseover="b()">글자</p><a href="javascript:alert(1)">링크</a>'
        "<script>alert(2)</script><!-- 주석 -->"
    )
    clean, removed = sanitize_report(raw, ())
    assert clean == "<p>글자</p><a>링크</a>"
    assert removed["img"] == 1 and removed["script"] == 1 and removed["iframe"] == 1
    assert removed["onclick 속성"] == 1 and removed["onmouseover 속성"] == 1
    assert removed["링크(허용되지 않은 주소)"] == 1 and removed["주석"] == 1


def test_wiki02_sanitizer_is_idempotent_and_masks() -> None:
    raw = BODY + '<h1>제목</h1><p style="color: blue">파랑</p><hr>'
    once = sanitize(raw)
    assert sanitize(once) == once
    assert PHONE not in once and "<h1>" not in once and "<h3>제목</h3>" in once


def test_wiki02_size_limit_200kb() -> None:
    with pytest.raises(RichHtmlError):
        sanitize_report("<p>" + "a" * (MAX_HTML_BYTES + 1) + "</p>", ())


def test_wiki05_clean_html_type_is_guarded() -> None:
    with pytest.raises(TypeError):
        CleanHtml("<p>x</p>", object())
    with pytest.raises(UserInputError):
        clean_html("<p>010-1234-<b>5678</b></p>")  # 서식 사이에 걸친 민감정보


# ---- C2: 복사 형식 7종 ----
def _draft(html: str = BODY) -> WeeklyDraft:
    return WeeklyDraft(week_start(datetime(2026, 11, 16, 10, 0, tzinfo=KST)), html=html)


def _masked_draft() -> WeeklyDraft:
    return _draft(sanitize(BODY))


def test_wiki05_formats_cover_seven_and_only_wiki_is_direct() -> None:
    assert [f.value for f in ExportFormat] == [
        "wiki",
        "wiki_source",
        "slack",
        "teams",
        "mail",
        "markdown",
        "text",
    ]
    assert [k for k, v in FORMATS.items() if v.direct] == [ExportFormat.WIKI]
    assert DEFAULT_FORMAT[Platform.TEAMS] is ExportFormat.TEAMS


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_wiki05_every_format_is_masked(fmt: ExportFormat) -> None:
    text, html = export_draft(_masked_draft(), TEMPLATE, AUTHOR, fmt)
    assert PHONE not in text and (html is None or PHONE not in html)
    assert "<script" not in text.lower() and (html is None or "<script" not in html.lower())
    assert (html is not None) is (FORMATS[fmt].has_html or fmt is ExportFormat.WIKI)


def test_wiki05_format_snapshots() -> None:
    clean = clean_html(
        "<h3>금주</h3><ul><li>a|b &lt;@U1&gt; @here</li></ul>"
        "<table><tr><th>구분</th><th>내용</th></tr><tr><td>x|y</td><td>*z*</td></tr></table>"
    )
    body = (
        "<h3>금주</h3><ul><li>a|b &lt;@U1&gt; @here</li></ul>"
        "<table><tr><th>구분</th><th>내용</th></tr><tr><td>x|y</td><td>*z*</td></tr></table>"
    )
    assert to_storage_xhtml(clean, AUTHOR) == "<h2>김가상</h2>" + body
    assert to_teams_html(clean, AUTHOR) == "<h2>김가상</h2>" + body
    mail = to_mail_html(clean, AUTHOR)
    assert mail.startswith("<div style=\"font-family: 'Malgun Gothic'")
    assert '<table style="border-collapse: collapse' in mail and "<td style=" in mail
    slack = to_slack_html(clean, AUTHOR)
    assert "@​here" in slack and "<table" not in slack  # 멘션 무력화, 표는 줄로
    assert to_markdown(clean, AUTHOR) == (
        "## 김가상\n\n### 금주\n\n- a|b \\<@U1\\> @here\n\n"
        "| 구분 | 내용 |\n| --- | --- |\n| x\\|y | \\*z\\* |\n"
    )


def test_wiki05_slack_text_neutralizes_mentions() -> None:
    text, html = export_draft(_masked_draft(), TEMPLATE, AUTHOR, ExportFormat.SLACK)
    assert "@here" not in text and "<@U123>" not in text
    assert html is not None and "@here" not in html


def test_wiki05_list_draft_exports_without_html() -> None:
    draft = WeeklyDraft(_draft().week_start, done=["완료 항목"], planned=["계획"], issues=[])
    text, html = export_draft(draft, TEMPLATE, AUTHOR, ExportFormat.MARKDOWN)
    assert "- 완료 항목" in text and html is None


def test_wiki05_fake_publish_target_records_only() -> None:
    fake = FakePublishTarget()
    fake.post("글자", "<p>글자</p>")
    assert fake.posts == [("글자", "<p>글자</p>")]


# ---- 참고 링크 플랫폼 판별 ----
@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://teams.microsoft.com/l/channel/x", Platform.TEAMS),
        ("https://outlook.office.com/mail/", Platform.MAIL),
        ("https://www.notion.so/page", Platform.MARKDOWN),
        ("https://github.com/org/repo/wiki", Platform.MARKDOWN),
        ("https://team.slack.com/archives/C1", Platform.SLACK),
        ("https://teams.microsoft.com.evil.invalid/x", Platform.WEB),  # 접미 위장
        ("https://notteams.microsoft.com/x", Platform.WEB),
    ],
)
def test_wiki01_detect_new_platforms(url: str, platform: Platform) -> None:
    assert detect_platform(url) is platform


# ---- 스키마 8: 플랫폼 CHECK 확장(데이터 보존) ----
NEW_LIST = "'slack', 'wiki', 'web', 'teams', 'mail', 'markdown'"
ADD_LINK = "INSERT INTO weekly_link (url, name, platform) VALUES ('https://x.invalid', 'n', ?)"


def test_wiki05_schema8_widens_platform_and_keeps_rows(tmp_path: Path) -> None:
    path = tmp_path / "v7.db"
    old = SCHEMA.replace(NEW_LIST, "'slack', 'wiki', 'web'")
    assert old != SCHEMA
    conn = sqlite3.connect(path)
    conn.executescript(old)
    conn.execute(
        "INSERT INTO weekly_link (id, url, name, platform) VALUES (1, ?, ?, 'wiki')",
        ("https://wiki.example.invalid/1", "합성 양식"),
    )
    conn.execute(
        "INSERT INTO weekly_publish_cache (week_start, platform, method, published_at, snapshot)"
        " VALUES ('2026-11-12', 'slack', 'clipboard', '2026-11-12T00:00:00+00:00', '마스킹 사본')"
    )
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ADD_LINK, ("teams",))
    conn.close()
    db = Database(path)
    assert db.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION == 8
    assert [r["name"] for r in db.query("SELECT name FROM weekly_link")] == ["합성 양식"]
    assert len(db.query("SELECT * FROM weekly_publish_cache")) == 1
    with db.transaction() as tx:
        tx.execute(ADD_LINK, ("teams",))
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as tx:
        tx.execute(ADD_LINK, ("fax",))
    db.close()
    again = Database(path)  # 두 번 열어도 다시 만들지 않는다
    assert len(again.query("SELECT * FROM weekly_link")) == 2
    again.close()


def test_wiki05_fake_publish_target_is_not_wired() -> None:
    src = Path(__file__).resolve().parents[2] / "src"
    users = [
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if "FakePublishTarget" in p.read_text("utf-8")
    ]
    assert users == ["integrations/publish/fake.py"]


@pytest.mark.parametrize(
    ("style", "kept"),
    [
        ("background-color: #ff0000", "background-color: #ff0000"),
        ("background-color: rgb(1,2,3)", "background-color: rgb(1,2,3)"),
        ("background-color: var(--x)", None),  # 16진·rgb()·이름 색만
        ("width: 50%", "width: 50%"),
        ("width: 10em", None),  # px·%만
        ("color: red; font-size: 40px", "color: red"),  # 허용 밖 속성만 뺀다
    ],
)
def test_wiki02_style_value_rules(style: str, kept: str | None) -> None:
    clean, removed = sanitize_report(f'<p style="{style}">a</p>', ())
    assert clean == (f'<p style="{kept}">a</p>' if kept else "<p>a</p>")
    assert bool(removed) is (kept != style)


def test_wiki02_form_object_embed_data_links_removed() -> None:
    raw = '<a href="data:text/html,x">d</a><form><input value="1">f</form><object>o</object><embed>'
    clean, removed = sanitize_report(raw, ())
    assert clean == "<a>d</a>"
    assert {"form", "object", "embed", "링크(허용되지 않은 주소)"} <= set(removed)
