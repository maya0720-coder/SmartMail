"""제안된 업무 트리아지 화면 테스트(TODO-01·02, 결정기록 0015, 명세 8장 ⑧⑨).

로컬 127.0.0.1 데모 서버(가짜 메일·가짜 어댑터)만 쓴다. 화면 캡처는 테스트 임시 폴더에만 저장한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.models import Task, TaskSource, TaskStatus
from fixtures.ui_server import chromium, csrf_post, demo_server
from storage.repositories import SqliteTaskRepository

XSS_TITLE = '<img src=x onerror="window.__xss=1"> 스크립트 제목 제안'


@pytest.mark.slow
def test_todo02_triage_keyboard_decisions_undo_and_highlight(tmp_path: Path) -> None:
    """⑧ A·S·D 키로 결정 → 다음 항목 선택, 되돌리기 토스트.

    요청 문장 <mark> 표시와 제목 속 스크립트 미실행도 확인한다.
    """
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + "/")
        csrf_post(page, base, "/api/login/later")
        csrf_post(page, base, "/api/mail/collect")
        repo = SqliteTaskRepository(container.db)
        repo.add(
            Task(
                XSS_TITLE,
                TaskStatus.SUGGESTED,
                TaskSource.MAIL,
                container.clock.now(),
                module_tag="일반업무",
            )
        )

        page.goto(base + "/suggestions")
        page.wait_for_selector("#tr-items .tr-item.sel")
        total = page.locator("#tr-items .tr-item").count()
        assert total >= 4
        page.locator("#tr-detail mark").first.wait_for()  # 요청 문장 하이라이트(<mark> 요소)
        assert page.evaluate("window.__xss") is None
        assert page.locator("#tr-items", has_text="스크립트 제목 제안").count() == 1

        first_id = page.locator("#tr-items .tr-item.sel").get_attribute("data-id")
        page.locator("body").press("a")  # 할 일로
        page.wait_for_function(
            f"() => document.querySelectorAll('#tr-items .tr-item').length === {total - 1}"
        )
        next_id = page.locator("#tr-items .tr-item.sel").get_attribute("data-id")
        assert next_id and next_id != first_id  # 다음 항목 자동 선택
        page.locator("#toast .toast-act").wait_for()
        assert "할 일로" in page.inner_text("#toast")

        page.click("#toast .toast-act")  # 되돌리기
        page.locator("#toast", has_text="되돌렸습니다").wait_for()
        page.wait_for_function(
            f"() => document.querySelectorAll('#tr-items .tr-item').length === {total}"
        )
        assert page.locator(f'#tr-items .tr-item[data-id="{first_id}"]').count() == 1

        page.locator("body").press("s")  # 나중에
        page.wait_for_function(
            f"() => document.querySelectorAll('#tr-items .tr-item').length === {total - 1}"
        )
        assert "나중에 1건" in page.inner_text("#tr-snoozed")
        page.locator("body").press("d")  # 업무 아님
        page.wait_for_function(
            f"() => document.querySelectorAll('#tr-items .tr-item').length === {total - 2}"
        )
        assert "업무 아님" in page.inner_text("#toast")

        page.locator("body").press("e")  # 제목 고치기
        page.locator("#tr-dtitle input").wait_for()
        page.keyboard.press("Escape")
        page.screenshot(path=str(tmp_path / "triage.png"))
        assert errors == []


@pytest.mark.slow
def test_todo02_dashboard_default_filter_all(tmp_path: Path) -> None:
    """⑨ 대시보드 기본 필터는 '전체'(무시만 제외), 제안됨도 보이고 배너도 표시(결정기록 0018)."""
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base + "/")
        csrf_post(page, base, "/api/login/later")
        csrf_post(page, base, "/api/mail/collect")
        csrf_post(page, base, "/api/tasks", {"title": "수동으로 넣은 할 일"})
        page.reload()
        page.wait_for_selector("#todo tr.row")
        assert page.locator("#filters button.on").get_attribute("data-f") == "all"
        statuses = page.locator("#todo tr.row .badge").all_inner_texts()
        assert "제안됨" in statuses  # 전체에는 제안됨도 보인다
        banner = page.locator("#sugg-banner")
        assert banner.is_visible()
        suggested = len([t for t in page.request.get(base + "/api/tasks?status=suggested").json()])
        assert f"정리할 제안 {suggested}건" in banner.inner_text()
        page.click('#filters button[data-f="todo"]')  # '할 일' 필터로 바꾸면 제안됨은 숨는다
        assert "제안됨" not in page.locator("#todo tr.row .badge").all_inner_texts()


@pytest.mark.slow
def test_todo01_narrow_drawer_tabs_and_progress(tmp_path: Path) -> None:
    """⑫ 900px 이하: 항목을 누르면 상세 서랍이 열리고 Esc로 닫힌다(포커스는 항목으로).

    탭(전체 n건)·진행 막대·'나중에 n건' 펼침도 함께 확인한다.
    """
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        page = browser.new_page(viewport={"width": 800, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + "/")
        csrf_post(page, base, "/api/login/later")
        csrf_post(page, base, "/api/mail/collect")
        page.goto(base + "/suggestions")
        page.wait_for_selector("#tr-items .tr-item.sel")
        total = page.locator("#tr-items .tr-item").count()
        assert page.locator("#tr-tabs button.on").inner_text() == f"전체 {total}"
        assert page.locator("#tr-items .tr-group").count() >= 1
        assert not page.locator("#tr-detail").is_visible()  # 좁은 화면: 서랍은 닫힌 상태

        second = page.locator("#tr-items .tr-item").nth(1)
        second_id = second.get_attribute("data-id")
        second.click()
        page.locator("body.tr-open").wait_for()
        page.locator("#tr-detail[role='dialog']").wait_for()
        assert page.evaluate("document.activeElement.id") == "tr-close"
        assert page.locator("#tr-detail", has_text="주간보고 예상 위치").count() == 1
        page.keyboard.press("Escape")
        page.locator("body:not(.tr-open)").wait_for()
        assert page.evaluate("document.activeElement.dataset.id") == second_id

        second.click()  # 배경(scrim)을 눌러도 닫힌다
        page.locator("body.tr-open").wait_for()
        page.mouse.click(10, 450)
        page.locator("body:not(.tr-open)").wait_for()

        page.locator("body").press("s")  # 나중에 → 진행 막대·'나중에 1건'
        page.locator("#tr-snoozed:not(.hidden)").wait_for()
        assert int(page.locator("#tr-progress").get_attribute("aria-valuenow") or "0") > 0
        page.click("#tr-snoozed")
        page.locator("#tr-items .tr-item.snoozed").wait_for()
        assert page.locator("#tr-snoozed").get_attribute("aria-expanded") == "true"
        page.screenshot(path=str(tmp_path / "triage_narrow.png"))
        assert errors == []
