"""패널 크기 조절·폭에 맞춘 재배치 화면 테스트(결정기록 0016, 디자인 명세 06 7장 ⑤~⑬).

로컬 127.0.0.1 데모 서버(가짜 메일·가짜 어댑터)만 쓴다. 외부 사이트에 접속하지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from domain.models import Task, TaskSource, TaskStatus
from fixtures.ui_server import chromium, csrf_post, demo_server
from storage.repositories import SqliteTaskRepository

WIDTH = "(s) => Math.round(document.querySelector(s).getBoundingClientRect().width)"
# 패널 안에 가로 스크롤이 없고, 보이는 자식이 패널 밖으로 나가지 않는다
FITS = """(s) => {
  const panel = document.querySelector(s);
  const box = panel.getBoundingClientRect();
  const scrolls = [panel, ...panel.querySelectorAll('*')].filter((el) => {
    const style = getComputedStyle(el);
    const clips = ['auto', 'scroll', 'hidden'].includes(style.overflowX);
    return !clips && style.display !== 'inline' && el.clientWidth > 0
      && el.scrollWidth > el.clientWidth + 1;
  }).map((el) => el.className || el.tagName);
  const outside = [...panel.querySelectorAll('*')].filter((el) => {
    if (el.offsetParent === null) return false;
    if (el.closest('.filters, .mail-body, .tr-mail, .bulk')) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && (r.right > box.right + 1 || r.left < box.left - 1);
  }).map((el) => el.className || el.tagName);
  return { scrolls, outside };
}"""
# 버튼 글자가 버튼 밖으로 나가지 않는다
BUTTONS_FIT = """(s) => [...document.querySelectorAll(s + ' .btn')]
  .filter((b) => b.offsetParent !== null && b.scrollWidth > b.clientWidth + 1)
  .map((b) => b.textContent)"""


def _start(page: Any, base: str, container: Any, tasks: int = 0) -> None:
    page.goto(base + "/")
    csrf_post(page, base, "/api/login/later")
    csrf_post(page, base, "/api/mail/collect")
    repo = SqliteTaskRepository(container.db)
    for index in range(tasks):
        repo.add(
            Task(
                f"합성 업무 {index:02d} 원가센터 배부 기준 변경 요청 확인",
                TaskStatus.WAITING,
                TaskSource.MANUAL,
                container.clock.now(),
                module_tag="일반업무",
                requester_dept="가상재무팀",
            )
        )


def _drag(page: Any, handle: str, dx: int) -> None:
    box = page.locator(handle).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx / 2, y, steps=4)
    page.mouse.move(x + dx, y, steps=4)
    page.mouse.up()
    page.wait_for_timeout(250)  # 프레임 반영 + 저장 요청


def _set_width(page: Any, css_var: str, px: int) -> None:
    page.evaluate(
        "([name, value]) => document.documentElement.style.setProperty(name, value + 'px')",
        [css_var, px],
    )
    page.wait_for_timeout(200)  # 컨테이너 쿼리·행 수 재계산


@pytest.mark.slow
def test_ui_dashboard_splitter_drag_keyboard_and_persist(tmp_path: Path) -> None:
    """⑤⑥⑦⑨ 끌기·최소/최대·키보드·저장/복원·초기화·서랍 구간 숨김."""
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        _start(page, base, container, tasks=30)
        page.goto(base + "/")
        page.wait_for_selector("#todo tr.row")
        assert page.evaluate(WIDTH, "#detail") == 320

        _drag(page, "#split-detail", -100)  # 왼쪽으로 끌면 상세가 커진다
        assert abs(page.evaluate(WIDTH, "#detail") - 420) <= 2
        assert not page.evaluate("document.body.classList.contains('resizing')")
        _drag(page, "#split-detail", -900)  # 최대 640px에서 멈춘다
        assert page.evaluate(WIDTH, "#detail") == 640
        _drag(page, "#split-detail", 900)  # 최소 280px에서 멈춘다
        assert page.evaluate(WIDTH, "#detail") == 280
        assert page.locator("#split-detail").get_attribute("aria-valuenow") == "280"
        assert page.locator("#split-detail").get_attribute("aria-valuemin") == "280"

        page.focus("#split-detail")  # ⑥ 키보드
        page.keyboard.press("ArrowLeft")
        assert page.evaluate(WIDTH, "#detail") == 296
        page.keyboard.press("Shift+ArrowLeft")
        assert page.evaluate(WIDTH, "#detail") == 360
        page.keyboard.press("ArrowRight")
        assert page.evaluate(WIDTH, "#detail") == 344
        assert page.locator("#split-detail").get_attribute("aria-valuenow") == "344"
        page.keyboard.press("End")
        assert page.evaluate(WIDTH, "#detail") == 640
        page.keyboard.press("Home")
        assert page.evaluate(WIDTH, "#detail") == 280
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(700)  # 키보드 조절은 400ms 뒤 한 번 저장

        page.reload()  # ⑦ 새로고침 후 유지
        page.wait_for_selector("#todo tr.row")
        assert page.evaluate(WIDTH, "#detail") == 312
        assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")

        # ⑨ 창을 줄이면 목록이 520px 아래로 줄지 않게 상세가 줄어든다
        page.focus("#split-detail")
        page.keyboard.press("End")
        page.wait_for_timeout(700)
        page.set_viewport_size({"width": 1120, "height": 900})
        page.wait_for_timeout(200)
        assert page.evaluate(WIDTH, ".todo-card") >= 520
        assert page.evaluate(WIDTH, "#detail") < 640
        page.set_viewport_size({"width": 1000, "height": 900})  # 서랍 구간: 손잡이 숨김
        assert not page.locator("#split-detail").is_visible()

        page.set_viewport_size({"width": 1440, "height": 900})
        page.reload()
        page.wait_for_selector("#todo tr.row")
        page.locator("#split-detail").dblclick()  # 더블클릭: 기본 폭
        page.wait_for_timeout(300)
        assert page.evaluate(WIDTH, "#detail") == 320
        page.reload()
        page.wait_for_selector("#todo tr.row")
        assert page.evaluate(WIDTH, "#detail") == 320
        assert errors == []


@pytest.mark.slow
def test_ui_settings_resets_layout(tmp_path: Path) -> None:
    """⑦ 설정 화면의 '화면 배치 초기화'로 저장한 폭을 지운다."""
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _start(page, base, container)
        page.goto(base + "/suggestions")
        page.wait_for_selector("#tr-items .tr-item.sel")
        assert page.evaluate(WIDTH, "#tr-list") == 380
        _drag(page, "#split-list", 100)  # 오른쪽으로 끌면 목록이 커진다
        assert abs(page.evaluate(WIDTH, "#tr-list") - 480) <= 2
        _drag(page, "#split-list", 900)
        assert page.evaluate(WIDTH, "#tr-list") == 560
        page.reload()
        page.wait_for_selector("#tr-items .tr-item.sel")
        assert page.evaluate(WIDTH, "#tr-list") == 560

        page.goto(base + "/settings")
        page.click("#btn-ui-reset")
        page.locator("#toast.show").wait_for()
        page.goto(base + "/suggestions")
        page.wait_for_selector("#tr-items .tr-item.sel")
        assert page.evaluate(WIDTH, "#tr-list") == 380


@pytest.mark.slow
@pytest.mark.parametrize("detail_w", [280, 340, 480, 640])
def test_ui_dashboard_reflow_has_no_overflow(tmp_path: Path, detail_w: int) -> None:
    """⑩⑪⑫⑬ 상세 폭마다 넘침이 없고, 목록 열을 숨기면 보조 줄에 보이며 행 수를 다시 계산한다."""
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _start(page, base, container, tasks=40)
        page.goto(base + "/")
        page.wait_for_selector("#todo tr.row")
        page.wait_for_timeout(300)
        rows_before = page.locator("#todo tr.row").count()
        _set_width(page, "--detail-w", detail_w)
        page.locator("#todo tr.row").first.click()
        page.wait_for_selector("#d-body .meta")
        page.wait_for_timeout(300)

        assert page.evaluate(WIDTH, "#detail") == detail_w
        for panel in ("#detail", ".todo-card"):
            result = page.evaluate(FITS, panel)
            assert result == {"scrolls": [], "outside": []}, (panel, result)
            assert page.evaluate(BUTTONS_FIT, panel) == [], panel
        # ⑪ 한 화면 안에서 행 높이가 일정하다
        heights = page.evaluate(
            "[...document.querySelectorAll('#todo tr.row')]"
            ".map(r => r.getBoundingClientRect().height)"
        )
        assert max(heights) - min(heights) <= 1
        # ⑫ 페이지 스크롤이 생기지 않고, 표가 목록 영역을 넘지 않는다
        assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")
        assert page.evaluate(
            "document.querySelector('#todo').getBoundingClientRect().bottom"
            " <= document.querySelector('#todo-wrap').getBoundingClientRect().bottom + 1"
        )
        list_width = page.evaluate(WIDTH, ".todo-card")
        dept_column = page.locator("#todo tr.row td.c-dept").first
        meta_dept = page.locator("#todo tr.row .t-meta .m-dept").first
        if list_width < 900:  # ⑬ 숨긴 열은 보조 줄로
            assert not dept_column.is_visible()
            # 기본 정렬이 최근 메일순이라(결정기록 0028) 첫 행의 숨긴 '요청 부서' 칸과 비교한다
            assert meta_dept.is_visible()
            assert meta_dept.inner_text() == (dept_column.text_content() or "").replace("-", "")
            assert heights[0] > 45 and page.locator("#todo tr.row").count() < rows_before
            mod_chip = page.locator("#todo tr.row .t-meta .m-mod").first
            assert mod_chip.is_visible() == (list_width < 700)
        else:
            assert dept_column.is_visible() and not meta_dept.is_visible()


@pytest.mark.slow
@pytest.mark.parametrize("list_w", [280, 340, 460, 560])
def test_ui_triage_reflow_has_no_overflow(tmp_path: Path, list_w: int) -> None:
    """⑩⑪ 제안 목록 폭마다 넘침이 없고 행 높이가 일정하다."""
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _start(page, base, container)
        page.goto(base + "/suggestions")
        page.wait_for_selector("#tr-items .tr-item.sel")
        _set_width(page, "--tr-list-w", list_w)
        page.wait_for_timeout(300)

        assert page.evaluate(WIDTH, "#tr-list") == list_w
        for panel in ("#tr-list", "#tr-detail"):
            result = page.evaluate(FITS, panel)
            assert result == {"scrolls": [], "outside": []}, (panel, result)
            assert page.evaluate(BUTTONS_FIT, panel) == [], panel
        heights = page.evaluate(
            "[...document.querySelectorAll('#tr-items .tr-item')]"
            ".map(r => Math.round(r.getBoundingClientRect().height))"
        )
        if list_w >= 460:  # 한 줄 배치
            assert max(heights) <= 46
        if list_w >= 340:  # 좁은 구간은 제목이 2줄까지라 행 높이가 달라질 수 있다
            assert max(heights) - min(heights) <= 1
        # 받은 시각이 다음 줄로 밀리지 않는다
        assert page.evaluate(
            "[...document.querySelectorAll('#tr-items .tm')]"
            ".every(e => e.getClientRects().length === 1)"
        )
        columns = page.evaluate(
            "getComputedStyle(document.querySelector('.tr-fields'))"
            ".gridTemplateColumns.split(' ').length"
        )
        detail_w = page.evaluate(WIDTH, "#tr-detail")
        assert columns == (4 if detail_w >= 900 else 2 if detail_w >= 640 else 1)
