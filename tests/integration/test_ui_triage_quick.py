"""빠른 정리 모드 화면 테스트(TODO-02, 결정기록 0015, 명세 8장 ⑬).

로컬 127.0.0.1 데모 서버(가짜 메일·가짜 어댑터)만 쓴다. 화면 캡처는 테스트 임시 폴더에만 저장한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.models import TaskStatus
from fixtures.ui_server import chromium, csrf_post, demo_server


@pytest.mark.slow
def test_todo02_quick_mode_keys_drag_buttons_and_done(tmp_path: Path) -> None:
    """⑬ Q로 진입 → ← → ↓ 키·끌기·버튼으로 결정 → 완료 화면 집계, E는 A안 상세로."""
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + "/")
        csrf_post(page, base, "/api/login/later")
        csrf_post(page, base, "/api/mail/collect")
        page.goto(base + "/suggestions")
        page.wait_for_selector("#tr-items .tr-item.sel")
        total = page.locator("#tr-items .tr-item").count()
        assert total >= 4

        page.locator("body").press("q")  # 진입(같은 키로 바로 꺼지지 않아야 함)
        page.locator("#q-card").wait_for()
        assert page.locator("body.quick-on").count() == 1
        assert not page.locator(".triage").is_visible()
        assert page.locator(".q-pos").inner_text() == f"1 / {total}"
        page.locator("#q-card mark").first.wait_for()
        page.screenshot(path=str(tmp_path / "quick_card.png"))

        page.keyboard.press("ArrowRight")  # 할 일로
        page.locator(".q-pos", has_text=f"2 / {total}").wait_for()
        page.keyboard.press("ArrowLeft")  # 업무 아님
        page.locator(".q-pos", has_text=f"3 / {total}").wait_for()
        page.keyboard.press("ArrowDown")  # 나중에
        page.locator(".q-pos", has_text=f"4 / {total}").wait_for()

        # 끌기: 오른쪽으로 150px → 할 일로
        box = page.locator("#q-card").bounding_box()
        assert box is not None
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.mouse.move(cx + 60, cy, steps=4)
        page.mouse.move(cx + 150, cy, steps=4)
        page.mouse.up()
        expected_approved = 2
        if total > 4:
            page.locator(".q-pos", has_text=f"5 / {total}").wait_for()
            # 짧은 끌기(110px 미만)는 결정하지 않고 제자리로
            box = page.locator("#q-card").bounding_box()
            assert box is not None
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.mouse.move(cx - 50, cy, steps=3)
            page.mouse.up()
            assert page.locator(".q-pos").inner_text() == f"5 / {total}"
            # 남은 카드는 버튼(끌기 대안)으로 할 일로
            for _ in range(total - 4):
                page.click("#q-approve")
                page.wait_for_timeout(300)
            expected_approved += total - 4
        page.locator("#q-done-sum").wait_for()
        summary = page.inner_text("#q-done-sum")
        assert summary == f"할 일 {expected_approved} · 나중에 1 · 업무 아님 1"
        assert page.locator("#q-home").inner_text() == "대시보드로"
        page.screenshot(path=str(tmp_path / "quick_done.png"))

        statuses = [t.status for t in container.tasks.list_tasks()]
        assert statuses.count(TaskStatus.DISMISSED) == 1
        assert sum(1 for t in container.tasks.list_tasks() if container.tasks.is_snoozed(t)) == 1

        page.keyboard.press("Escape")  # 종료 → 목록
        page.locator("body:not(.quick-on)").wait_for()
        page.locator(".triage").wait_for()
        assert errors == []


@pytest.mark.slow
def test_todo02_quick_mode_e_opens_detail(tmp_path: Path) -> None:
    """⑬ E: 빠른 모드를 끝내고 같은 항목을 A안 상세로 연다(빠른 모드에서는 편집 없음)."""
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base + "/")
        csrf_post(page, base, "/api/login/later")
        csrf_post(page, base, "/api/mail/collect")
        page.goto(base + "/suggestions")
        page.wait_for_selector("#tr-items .tr-item.sel")
        page.click("#tr-quick-btn")
        page.locator("#q-card").wait_for()
        page.keyboard.press("ArrowDown")
        page.locator(".q-pos", has_text="2 / ").wait_for()
        title = page.locator(".q-title").inner_text()
        page.keyboard.press("e")
        page.locator("body:not(.quick-on)").wait_for()
        page.locator("#tr-dtitle h3", has_text=title).wait_for()
        assert page.locator("#tr-items .tr-item.sel .tr-title").inner_text() == title
