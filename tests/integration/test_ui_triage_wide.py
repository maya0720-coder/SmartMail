"""제안된 업무 화면 배치 테스트(결정기록 0033).

목록 폭 상한(N1), 넓게 보기와 폭 프리셋(U3), 메일 높이(U4)를 확인한다.

로컬 127.0.0.1 데모 서버(가짜 메일·가짜 어댑터)만 쓴다. 외부 사이트에 접속하지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fixtures.ui_server import chromium, csrf_post, demo_server

WIDTH = "(s) => Math.round(document.querySelector(s).getBoundingClientRect().width)"
RECT = (
    "(s) => { const r = document.querySelector(s).getBoundingClientRect();"
    " return [r.top, r.bottom]; }"
)


def _open(page: Any, base: str) -> None:
    page.goto(base + "/")
    csrf_post(page, base, "/api/login/later")
    csrf_post(page, base, "/api/mail/collect")
    page.goto(base + "/suggestions")
    page.wait_for_selector("#tr-items .tr-item.sel")
    page.wait_for_selector("#tr-detail .tr-mail")


def _drag(page: Any, handle: str, dx: int) -> None:
    box = page.locator(handle).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx / 2, y, steps=4)
    page.mouse.move(x + dx, y, steps=4)
    page.mouse.up()
    page.wait_for_timeout(300)


@pytest.mark.slow
def test_ui_n1_list_widens_past_old_cap(tmp_path: Path) -> None:
    """N1: 1920px 화면에서 목록을 900px 이상 넓힌다.

    상세는 420px 아래로 줄지 않고, 창을 줄이면 저장 폭도 따라 줄어든다.
    """
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        _open(page, base)
        _drag(page, "#split-list", 600)
        assert page.evaluate(WIDTH, "#tr-list") >= 900
        _drag(page, "#split-list", 2000)
        assert page.evaluate(WIDTH, "#tr-detail") >= 420
        page.focus("#split-list")
        page.keyboard.press("End")  # 새 최대값(화면 폭 비례)
        end_width = page.evaluate(WIDTH, "#tr-list")
        assert end_width == int(page.locator("#split-list").get_attribute("aria-valuemax"))
        assert end_width > 900
        page.wait_for_timeout(700)

        page.set_viewport_size({"width": 1280, "height": 900})  # 창을 줄이면 저장 폭이 자동 축소
        page.wait_for_timeout(300)
        assert page.evaluate(WIDTH, "#tr-list") < end_width
        assert page.evaluate(WIDTH, "#tr-detail") >= 420

        page.set_viewport_size({"width": 1920, "height": 1080})
        page.reload()
        page.wait_for_selector("#tr-items .tr-item.sel")
        assert page.evaluate(WIDTH, "#tr-list") == end_width  # 저장 값은 그대로
        page.locator("#split-list").dblclick()  # 기본 폭
        page.wait_for_timeout(300)
        assert page.evaluate(WIDTH, "#tr-list") == 380
        assert errors == []


@pytest.mark.slow
def test_ui_u3_wide_toggle_and_presets(tmp_path: Path) -> None:
    """U3: 넓게 보기(버튼·W)를 켜고 끄며, 새로고침 후에도 유지된다.

    손잡이 우클릭(또는 Shift+F10) 프리셋으로 폭을 고른다.
    """
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        _open(page, base)
        normal_detail = page.evaluate(WIDTH, "#tr-detail")

        page.click(".tr-wide-btn")
        page.wait_for_timeout(300)
        assert page.evaluate("document.body.classList.contains('tr-wide')")
        assert page.evaluate(WIDTH, "#tr-list") == 48
        assert page.evaluate(WIDTH, "#tr-detail") > normal_detail + 300
        assert page.locator(".tr-wide-btn").get_attribute("aria-pressed") == "true"
        assert page.locator("#tr-rail").is_visible()
        assert not page.locator("#tr-items").is_visible()

        page.reload()  # 서버 설정으로 유지(브라우저 저장소 사용 안 함)
        page.wait_for_selector("#tr-detail .tr-mail")
        assert page.evaluate("document.body.classList.contains('tr-wide')")
        assert page.evaluate("localStorage.length") == 0

        page.keyboard.press("ArrowDown")  # 접힌 상태에서도 다음 제안으로 이동
        page.keyboard.press("w")  # 단축키로 복귀
        page.wait_for_timeout(300)
        assert not page.evaluate("document.body.classList.contains('tr-wide')")
        assert page.evaluate(WIDTH, "#tr-list") == 380
        page.reload()
        page.wait_for_selector("#tr-items .tr-item.sel")
        assert not page.evaluate("document.body.classList.contains('tr-wide')")

        page.locator("#split-list").click(button="right")  # 폭 프리셋
        page.wait_for_selector(".split-menu")
        assert page.locator(".split-menu-item").all_inner_texts() == [
            "좁게 30%",
            "보통 40%",
            "넓게 55%",
            "기본 폭",
        ]
        total = page.evaluate(WIDTH, "#tr-list") + page.evaluate(WIDTH, "#tr-detail")
        page.click(".split-menu-item >> nth=2")
        page.wait_for_timeout(500)
        assert abs(page.evaluate(WIDTH, "#tr-list") - total * 0.55) <= 2
        assert page.locator(".split-menu").count() == 0
        page.reload()
        page.wait_for_selector("#tr-items .tr-item.sel")
        assert abs(page.evaluate(WIDTH, "#tr-list") - total * 0.55) <= 2

        page.focus("#split-list")  # 키보드로도 연다(Shift+F10), Esc로 닫는다
        page.keyboard.press("Shift+F10")
        page.wait_for_selector(".split-menu")
        page.keyboard.press("Escape")
        assert page.locator(".split-menu").count() == 0
        assert errors == []


@pytest.mark.slow
def test_ui_u4_mail_fills_detail_height(tmp_path: Path) -> None:
    """U4: 1080p에서 메일 영역이 상세 하단까지 찬다.

    작은 화면에서도 200px 이상이고, 하이라이트 문장이 처음부터 보인다.
    """
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        _open(page, base)
        page.wait_for_timeout(300)
        _, mail_bottom = page.evaluate(RECT, "#tr-detail .tr-mail")
        actions_top, _ = page.evaluate(RECT, "#tr-detail .tr-actions")
        past_top, _ = page.evaluate(RECT, "#tr-detail .tr-sec-past")
        # 메일 아래에는 접힌 '비슷한 지난 업무' 한 줄과 버튼 줄만 남는다
        assert 0 <= past_top - mail_bottom <= 16
        assert actions_top - mail_bottom <= 80
        assert page.evaluate(
            "document.querySelector('#tr-detail').scrollHeight"
            " <= document.querySelector('#tr-detail').clientHeight + 1"
        )

        page.click("#tr-detail .tr-sec-past summary")  # 접기·펼치기
        assert page.locator("#tr-detail .tr-sec-past").get_attribute("open") is not None

        mark = page.locator("#tr-detail .tr-mail mark").first
        if mark.count():  # 하이라이트가 있으면 메일 영역 안에서 보인다
            box_top, box_bottom = page.evaluate(RECT, "#tr-detail .tr-mail")
            mark_box = mark.bounding_box()
            assert box_top <= mark_box["y"] and mark_box["y"] + mark_box["height"] <= box_bottom

        page.click("#tr-mail-full")  # 크게 보기
        page.wait_for_selector("#dlg-mail.show .tr-mail.full")
        assert page.evaluate(WIDTH, "#dlg-mail .tr-mail") > page.evaluate(
            WIDTH, "#tr-detail .tr-mail"
        )
        page.keyboard.press("Escape")
        assert not page.locator("#dlg-mail").is_visible()
        assert page.evaluate("document.activeElement.id") == "tr-mail-full"

        page.set_viewport_size({"width": 1100, "height": 720})  # 작은 화면: 최소 200px
        page.wait_for_timeout(300)
        top, bottom = page.evaluate(RECT, "#tr-detail .tr-mail")
        assert bottom - top >= 200
