"""체크 유지·전체/묶음 선택 화면 테스트(B1·B2, TODO-02, 결정기록 0040).

로컬 127.0.0.1 데모 서버(가짜 메일·가짜 어댑터)만 쓴다. 제안·업무는 합성 제목으로 직접 만든다.
대시보드와 제안된 업무 화면의 클릭·체크 동작이 같은지 같은 시나리오로 확인한다.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from domain.models import Task, TaskSource, TaskStatus
from fixtures.ui_server import chromium, demo_server
from storage.repositories import SqliteTaskRepository

TR_PICKED = (
    "() => [...document.querySelectorAll('#tr-items .tr-item.picked')].map(li => li.dataset.id)"
)
DB_PICKED = "() => [...document.querySelectorAll('#todo tr.row .cb:checked')].length"
MIXED = "(sel) => { const b = document.querySelector(sel); return [b.checked, b.indeterminate]; }"


def _seed(
    container: Any,
    count: int,
    *,
    status: TaskStatus = TaskStatus.SUGGESTED,
    tag: str = "일반업무",
    days_ago: int = 0,
    prefix: str = "합성 제안",
) -> list[int]:
    repo = SqliteTaskRepository(container.db)
    now = container.clock.now() - timedelta(days=days_ago)
    ids = []
    for n in range(count):
        task = repo.add(
            Task(
                f"{prefix} {tag} {n:03d}",
                status,
                TaskSource.MAIL,
                now - timedelta(minutes=n),
                module_tag=tag,
            )
        )
        assert task.id is not None
        ids.append(task.id)
    return ids


def _page(browser: Any) -> tuple[Any, list[str]]:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return page, errors


def _open_triage(page: Any, base: str) -> None:
    page.goto(base + "/suggestions")
    page.wait_for_selector("#tr-items .tr-item.sel")


def _check(page: Any, index: int) -> None:
    page.locator("#tr-items .tr-item .tr-check").nth(index).click()


@pytest.mark.slow
def test_ui_todo02_row_click_keeps_checks_and_tabs_pages(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        _seed(container, 22)
        _seed(container, 2, tag="전자결재")
        page, errors = _page(browser)
        _open_triage(page, base)
        for index in (0, 1, 2):
            _check(page, index)
        picked = page.evaluate(TR_PICKED)
        assert len(picked) == 3 and "3건 선택" in page.inner_text("#bulk-count")
        # 다른 행 본문 클릭 → 상세만 바뀌고 체크 3건 유지(상세도 함께 보임)
        target = page.locator("#tr-items .tr-item").nth(5)
        title = target.locator(".tr-title").inner_text()
        target.locator(".tr-title").click()
        page.wait_for_selector(f"#tr-dtitle h3:has-text('{title}')")
        assert page.evaluate(TR_PICKED) == picked
        assert page.locator("#tr-bulk.on").count() == 1
        # 다음 페이지로 갔다 와도 유지
        page.locator("#tr-pager button", has_text="2").click()
        page.locator("#tr-pager button", has_text="1").click()
        assert page.evaluate(TR_PICKED) == picked
        # 탭을 바꿔도 유지, 바에 '다른 탭 포함'
        page.locator("#tr-tabs button", has_text="전자결재").click()
        assert "3건 선택(다른 탭 포함)" in page.inner_text("#bulk-count")
        page.locator("#tr-tabs button", has_text="전체").click()
        assert page.evaluate(TR_PICKED) == picked
        # Esc로 전체 해제
        page.focus("#tr-items")
        page.keyboard.press("Escape")
        assert page.evaluate(TR_PICKED) == []
        assert page.locator("#tr-bulk.on").count() == 0
        assert errors == []


@pytest.mark.slow
def test_ui_todo02_group_and_head_checks_three_state(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        _seed(container, 25, days_ago=20, prefix="합성 지난 제안")  # '이전' 묶음, 2페이지까지
        today = _seed(container, 3)  # 나중에 만든 것이 목록 앞(1페이지)
        page, errors = _page(browser)
        _open_triage(page, base)
        today_box = "#tr-items .tr-group-check[data-group='today']"
        page.click(today_box)
        assert sorted(int(i) for i in page.evaluate(TR_PICKED)) == sorted(today)
        assert page.evaluate(MIXED, today_box) == [True, False]
        _check(page, 0)  # 하나 해제 → 일부
        assert page.evaluate(MIXED, today_box) == [False, True]
        assert page.get_attribute(today_box, "aria-checked") == "mixed"
        assert page.evaluate(MIXED, "#tr-all") == [False, True]
        # '이전' 묶음은 모든 페이지 포함(현재 탭)
        page.click("#tr-items .tr-group-check[data-group='older']")
        assert "27건 선택" in page.inner_text("#bulk-count")
        # 머리 체크: 현재 페이지 20건 3상태 → 누르면 페이지 전체 체크
        page.click("#tr-all")
        assert page.evaluate(MIXED, "#tr-all") == [True, False]
        # 머리 다시 누르면 현재 페이지만 해제(다음 페이지 체크는 남음)
        page.click("#tr-all")
        assert "8건 선택" in page.inner_text("#bulk-count")
        # ▾ 전체 해제
        page.click("#tr-all-menu")
        page.click("#bulk-none")
        assert page.evaluate(TR_PICKED) == []
        assert errors == []


@pytest.mark.slow
def test_ui_todo02_select_all_caps_at_limit_and_prunes(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        _seed(container, 103)
        page, errors = _page(browser)
        _open_triage(page, base)
        page.click("#tr-all-menu")
        page.click("#bulk-all-tabs")
        page.wait_for_selector("#toast.show")
        assert "100건까지 선택했습니다" in page.inner_text("#toast")
        assert "100건 선택" in page.inner_text("#bulk-count")
        page.click("#tr-all-menu")
        page.click("#bulk-none")
        # 3건 체크 → 일괄 '할 일로' → 처리된 항목은 체크에서 빠진다
        for index in (0, 1, 2):
            _check(page, index)
        page.click("#bulk-approve")
        page.wait_for_selector("#tr-bulk:not(.on)", state="attached")
        assert page.evaluate(TR_PICKED) == []
        assert len(container.tasks.list_tasks([TaskStatus.SUGGESTED])) == 100
        assert errors == []


@pytest.mark.slow
def test_ui_todo02_dashboard_same_click_check_rules(tmp_path: Path) -> None:
    """대시보드도 같은 규칙: 행 클릭은 체크 유지, Esc 전체 해제, ▾ 필터 전체 선택(한도 안내)."""
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        _seed(container, 103, status=TaskStatus.IN_PROGRESS, prefix="합성 진행")
        page, errors = _page(browser)
        page.goto(base + "/")
        page.wait_for_selector("#todo tr.row")
        boxes = page.locator("#todo tr.row .cb")
        for index in (0, 1, 2):
            boxes.nth(index).click()
        assert page.evaluate(DB_PICKED) == 3
        page.locator("#todo tr.row").nth(4).locator(".c-title").click()
        page.wait_for_selector("#todo tr.row.sel")
        assert page.evaluate(DB_PICKED) == 3
        page.keyboard.press("Escape")  # 서랍이 없는 넓은 화면: 체크 전체 해제
        assert page.evaluate(DB_PICKED) == 0
        page.click("#todo-all-menu")
        page.locator("#bulk-menu button", has_text="이 필터 전체 선택").click()
        page.wait_for_selector("#toast.show")
        assert "100건까지 선택했습니다" in page.inner_text("#toast")
        assert "100건 선택" in page.inner_text("#bulk")
        assert errors == []
