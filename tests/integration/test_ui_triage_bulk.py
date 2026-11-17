"""제안 일괄 처리 화면 테스트(U2, TODO-02, 결정기록 0036).

로컬 127.0.0.1 데모 서버(가짜 메일·가짜 어댑터)만 쓴다. 외부 사이트에 접속하지 않는다.
제안은 합성 제목으로 직접 만든다.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from domain.models import Task, TaskSource, TaskStatus
from fixtures.ui_server import chromium, demo_server
from storage.repositories import SqliteTaskRepository

PICKED = (
    "() => [...document.querySelectorAll('#tr-items .tr-item.picked')].map(li => li.dataset.id)"
)
PAGE_IDS = "() => [...document.querySelectorAll('#tr-items .tr-item')].map(li => li.dataset.id)"


def _seed(container: Any, count: int) -> list[int]:
    repo = SqliteTaskRepository(container.db)
    now = container.clock.now()
    ids = []
    for n in range(count):
        task = repo.add(
            Task(
                f"합성 제안 {n:02d}",
                TaskStatus.SUGGESTED,
                TaskSource.MAIL,
                now - timedelta(minutes=n),
                module_tag="일반업무",
            )
        )
        assert task.id is not None
        ids.append(task.id)
    return ids


def _open(page: Any, base: str) -> None:
    page.goto(base + "/suggestions")
    page.wait_for_selector("#tr-items .tr-item.sel")


def _row(page: Any, index: int) -> Any:
    return page.locator("#tr-items .tr-item").nth(index)


@pytest.mark.slow
def test_ui_todo02_mouse_select_bulk_and_undo(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        _seed(container, 25)  # 한 페이지 20건 + 다음 페이지 5건
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        _open(page, base)
        page_ids = page.evaluate(PAGE_IDS)
        assert len(page_ids) == 20
        # Ctrl+클릭: 누른 항목만 체크(지금 보는 항목은 자동으로 넣지 않음, 0040) → 일괄 바
        _row(page, 2).click(modifiers=["Control"])
        page.wait_for_selector("#bulk-bar")
        assert "1건 선택" in page.inner_text("#bulk-count")
        # Shift+클릭 범위는 현재 페이지 안
        _row(page, 19).click(modifiers=["Shift"])
        picked = page.evaluate(PICKED)
        assert picked == page_ids[2:20]
        # Ctrl+A: 현재 페이지 전체(다음 페이지 5건은 넣지 않음)
        page.focus("#tr-items")
        page.keyboard.press("Control+a")
        assert page.evaluate(PICKED) == page_ids
        assert "20건 선택" in page.inner_text("#bulk-count")
        # ▾ 이 탭 전체 선택: 모든 페이지
        page.click("#tr-all-menu")
        page.click("#bulk-tab-all")
        assert "25건 선택" in page.inner_text("#bulk-count")
        page.click("#bulk-approve")
        page.wait_for_selector("#toast.show")
        assert "25건을 할 일로 옮겼습니다" in page.inner_text("#toast")
        assert container.tasks.list_tasks([TaskStatus.SUGGESTED]) == []
        # 되돌리기 1번으로 전부 원복
        page.click("#toast button")
        # CSP 때문에 wait_for_function 대신 폴링한다
        for _ in range(50):
            if len(container.tasks.list_tasks([TaskStatus.SUGGESTED])) == 25:
                break
            page.wait_for_timeout(100)
        assert len(container.tasks.list_tasks([TaskStatus.SUGGESTED])) == 25
        page.wait_for_selector("#tr-items .tr-item >> nth=19")
        assert errors == []


@pytest.mark.slow
def test_ui_todo02_keyboard_only_bulk(tmp_path: Path) -> None:
    """키보드만으로: Shift+↓ 범위 → D(업무 아님) → Esc 선택 해제."""
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        ids = _seed(container, 5)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _open(page, base)
        page.focus("#tr-items")
        page.keyboard.press("Shift+ArrowDown")
        page.keyboard.press("Shift+ArrowDown")
        page.wait_for_selector("#bulk-bar")
        assert "3건 선택" in page.inner_text("#bulk-count")
        page.keyboard.press("Escape")
        page.wait_for_selector("#bulk-bar", state="detached")
        assert page.evaluate(PICKED) == []
        # Space로 하나씩 고르고 D로 일괄 처리
        page.keyboard.press("Space")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Space")
        page.wait_for_selector("#bulk-bar")
        page.keyboard.press("d")
        page.wait_for_selector("#toast.show")
        assert "2건을 업무 아님으로 정리했습니다" in page.inner_text("#toast")
        left = {t.id for t in container.tasks.list_tasks([TaskStatus.SUGGESTED])}
        assert len(left) == 3 and left < set(ids)


@pytest.mark.slow
def test_ui_todo02_range_follows_group_order(tmp_path: Path) -> None:
    """묶음(오늘·이전)이 섞여도 Shift 범위는 화면에 보이는 순서를 따른다(자정 직후 회귀)."""
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        repo = SqliteTaskRepository(container.db)
        now = container.clock.now()
        for n, age in enumerate([0, 0, 0, 30, 30, 30]):  # 먼저 만든(작은 id) 쪽이 오늘
            repo.add(
                Task(
                    f"합성 묶음 {n}",
                    TaskStatus.SUGGESTED,
                    TaskSource.MAIL,
                    now - timedelta(days=age, minutes=n),
                    module_tag="일반업무",
                )
            )
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _open(page, base)
        page_ids = page.evaluate(PAGE_IDS)
        assert page.locator("#tr-items .tr-item.sel").get_attribute("data-id") == page_ids[0]
        _row(page, 1).click(modifiers=["Control"])
        _row(page, 4).click(modifiers=["Shift"])
        assert page.evaluate(PICKED) == page_ids[1:5]
