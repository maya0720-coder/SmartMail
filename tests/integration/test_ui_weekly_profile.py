"""조직 양식 프로필·지난주 이월·마감 알림 화면 테스트(N4·U5·U6, WIKI-01~03, 결정기록 0037).

로컬 127.0.0.1 데모 서버(가짜 위키)만 쓴다. 외부 사이트에 접속하지 않는다. 양식은 합성 HTML이다.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from domain.calendar import week_start
from domain.clock import SystemClock
from domain.weekly.remind import WeeklyReminder, parse_remind
from domain.weekly.service import WikiPage
from fixtures.ui_server import chromium, csrf_post, demo_server
from integrations.wiki.fake import FAKE_BASE, FakeWiki

OWN = "데모 사용자"
PAGE = (
    f"<h2>{OWN}</h2><h3>금주 실적</h3><p>※ 진척률은 % 단위</p>"
    "<table><tr><th>업무명</th><th>요청 부서</th><th>진척</th></tr>"
    "<tr><td>지난 데이터</td><td>합성팀</td><td>50%</td></tr></table>"
    "<h3>차주 계획</h3><ul><li>지난 계획</li></ul>"
)
PASTE = (
    "<h3>실적 요약</h3><table><tr><th>구분</th><th>내용</th></tr>"
    "<tr><td>a</td><td>b</td></tr></table>"
)
DISPATCH_PASTE = (
    "(html) => { const dt = new DataTransfer(); dt.setData('text/html', html);"
    " document.getElementById('pf-paste').dispatchEvent("
    "new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }"
)


def _wiki_link(page: Any, base: str, container: Any) -> int:
    wiki = container.weekly._wiki  # noqa: SLF001 - 데모 가짜 위키
    assert isinstance(wiki, FakeWiki)
    wiki.pages["양식"] = WikiPage("7", "양식", 1, PAGE, FAKE_BASE + "7")
    page.goto(base + "/settings")
    link = csrf_post(page, base, "/api/weekly/links", {"url": FAKE_BASE + "7", "name": "합성 양식"})
    return int(link["id"])


@pytest.mark.slow
def test_ui_wiki02_register_profile_from_wiki_and_paste(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        link_id = _wiki_link(page, base, container)
        slack = csrf_post(page, base, "/api/weekly/links", {"url": "https://team.slack.com/x"})
        page.goto(base + "/settings")
        page.locator("#wl-list .wl-item", has_text="합성 양식").locator(
            "button", has_text="위키에서 양식 읽기"
        ).click()
        page.wait_for_selector("#dlg-pf.show")
        assert page.locator("#pf-blocks select").count() == 1 + 3 + 1  # 머리글 2 + 표 열 3
        page.locator("#pf-blocks select[data-head]").first.select_option("issues")
        page.click("#pf-save")
        page.wait_for_selector("#dlg-pf.show", state="hidden")
        profile = container.weekly_profiles.get(link_id)
        assert profile is not None and profile.bindings.headings[0] == "issues"
        page.wait_for_selector("#wl-list .chip:has-text('양식 등록됨')")
        # 붙여넣기: HTML은 화면에 그리지 않고 서버로만 보낸다
        page.locator("#wl-list .wl-item", has_text="slack").locator(
            "button", has_text="양식 붙여넣기"
        ).click()
        page.wait_for_selector("#dlg-pf-paste.show")
        page.evaluate(DISPATCH_PASTE, PASTE)
        assert page.locator("#pf-paste table").count() == 0
        page.click("#pf-paste-ok")
        page.wait_for_selector("#dlg-pf.show")
        pasted = container.weekly_profiles.get(int(slack["id"]))
        assert pasted is not None and pasted.blocks[1].columns == ("구분", "내용")
        assert errors == []


@pytest.mark.slow
def test_ui_wiki02_structure_lock_compare_restore_and_carry(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("dialog", lambda dialog: dialog.accept())
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        link_id = _wiki_link(page, base, container)
        csrf_post(page, base, f"/api/weekly/links/{link_id}/profile/import")
        last = week_start(week_start(container.clock.now()) - timedelta(days=1))
        container.weekly_cache.add(
            last,
            "slack",
            "clipboard",
            None,
            container.clock.now(),
            "*차주 계획*\n• 이월할 합성 계획\n",
        )
        page.goto(base + "/weekly")
        page.wait_for_selector("#wk-editor h3")
        assert page.is_visible("#wk-lock") is False  # 기본 양식에는 잠금 도구 없음
        page.select_option("#wk-link", str(link_id))
        page.wait_for_selector("#wk-lock:not(.hidden)")
        heading = page.locator("#wk-editor h3").first
        assert heading.get_attribute("contenteditable") == "false"
        assert page.locator("#wk-editor th.pf-locked").count() == 3
        # 잠금 해제 후 머리글을 바꾸면 비교에서 표시되고, 되돌리면 같아진다
        page.click("#wk-lock")
        assert heading.get_attribute("contenteditable") is None
        page.evaluate(
            "() => { document.querySelector('#wk-editor h3').textContent = '바꾼 머리글'; }"
        )
        page.click("#wk-compare")
        assert page.locator("#wk-editor .pf-diff").count() >= 1
        assert "양식과 다른 곳" in page.inner_text("#toast")
        page.click("#wk-restore")
        page.click("#wk-compare")
        assert page.locator("#wk-editor .pf-diff").count() == 0
        assert "양식 구조와 같습니다" in page.inner_text("#toast")
        assert page.locator("#wk-editor h3").first.get_attribute("contenteditable") == "false"
        # 지난주 이월
        page.click("#btn-carry")
        page.wait_for_selector("#dlg-carry.show")
        assert "게시 캐시" in page.inner_text("#carry-src")
        page.click("#carry-ok")
        assert "이월할 합성 계획" in page.inner_text("#wk-editor")
        assert errors == []


@pytest.mark.slow
def test_ui_wiki03_deadline_banner(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        # 이번 주 시작(목 00:00)을 마감으로: 항상 지난 시각. 게시 기록 없음
        container.reminder = WeeklyReminder(
            parse_remind("THU 00:00"), SystemClock(), lambda _w: False
        )
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(base + "/")
        page.wait_for_selector("#remind-bar:not(.hidden)")
        assert "아직 게시하지 않았습니다" in page.inner_text("#remind-bar")
        page.click("#remind-hide")
        page.wait_for_selector("#remind-bar.hidden", state="attached")
        page.goto(base + "/history")
        page.wait_for_timeout(300)
        assert page.locator("#remind-bar.hidden").count() == 1
