"""화면 여백 통일 테스트(결정기록 0016, 디자인 명세 designMD/06 7장 ①~④).

로컬 127.0.0.1 데모 서버(가짜 메일·가짜 어댑터)만 쓴다. 외부 사이트에 접속하지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fixtures.ui_server import chromium, csrf_post, demo_server

PAGES = ("/", "/suggestions", "/weekly", "/history", "/settings")
TOLERANCE = 1.0  # px
PAGE_TOP = 14  # 메뉴 바 아래 ~ 제목 영역
HEAD_HEIGHT = 36  # 제목 영역 높이
HEAD_GAP = 12  # 제목 영역 ~ 첫 카드
TITLE_SIZE = "20px"

MEASURE = """() => {
  const rect = (el) => el.getBoundingClientRect();
  const nav = rect(document.querySelector('.topnav'));
  const head = rect(document.querySelector('.page-head'));
  const body = document.querySelector('.page-body');
  const first = [...body.children].find((el) => el.offsetParent !== null);
  return {
    navLeft: nav.left, navRight: nav.right, navBottom: nav.bottom,
    headLeft: head.left, headRight: head.right, headTop: head.top, headHeight: head.height,
    headBottom: head.bottom, firstTop: rect(first).top, firstLeft: rect(first).left,
    titleSize: getComputedStyle(document.querySelector('.page-head h2')).fontSize,
    scrollX: document.documentElement.scrollWidth - window.innerWidth,
  };
}"""


def _open(page: Any, base: str, path: str) -> dict[str, Any]:
    page.goto(base + path)
    page.wait_for_selector(".page-body")
    page.wait_for_timeout(300)  # 목록·지표를 그린 뒤 측정
    measured: dict[str, Any] = page.evaluate(MEASURE)
    return measured


@pytest.mark.slow
@pytest.mark.parametrize("viewport", [(1440, 900), (1920, 1080)])
def test_ui_layout_margins_are_same_on_every_tab(tmp_path: Path, viewport: tuple[int, int]) -> None:
    """①②④ 모든 탭의 본문 왼쪽 선·제목 위 여백·제목 크기·첫 카드 간격이 같다."""
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        page.goto(base + "/")
        csrf_post(page, base, "/api/login/later")
        csrf_post(page, base, "/api/mail/collect")
        lefts: list[float] = []
        for path in PAGES:
            m = _open(page, base, path)
            # ① 본문과 메뉴 바의 좌우 선이 같다
            assert abs(m["headLeft"] - m["navLeft"]) <= TOLERANCE, path
            assert abs(m["headRight"] - m["navRight"]) <= TOLERANCE, path
            assert abs(m["firstLeft"] - m["navLeft"]) <= TOLERANCE, path
            # ② 메뉴 바 아래~제목 14px, 제목 20px, 제목 영역 36px, 제목 영역~첫 카드 12px
            assert abs(m["headTop"] - m["navBottom"] - PAGE_TOP) <= TOLERANCE, path
            assert m["titleSize"] == TITLE_SIZE, path
            assert abs(m["headHeight"] - HEAD_HEIGHT) <= TOLERANCE, path  # ③ 포함(제안된 업무)
            assert abs(m["firstTop"] - m["headBottom"] - HEAD_GAP) <= TOLERANCE, path
            assert m["scrollX"] <= 0, path  # 가로 스크롤 없음
            lefts.append(m["headLeft"])
        # ④ 탭을 바꿔도 본문이 좌우로 움직이지 않는다
        assert max(lefts) - min(lefts) <= TOLERANCE


@pytest.mark.slow
def test_ui_layout_triage_is_fixed_shell_and_wraps_when_narrow(tmp_path: Path) -> None:
    """③ 제안된 업무: 1440px는 한 줄·화면 고정형, 1100px 이하는 탭이 둘째 줄."""
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base + "/")
        csrf_post(page, base, "/api/login/later")
        csrf_post(page, base, "/api/mail/collect")
        _open(page, base, "/suggestions")
        page.wait_for_selector("#tr-items .tr-item.sel")
        # 페이지 스크롤 없이 목록과 상세가 화면 아래까지 찬다
        assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")
        bottoms = page.evaluate(
            "['.tr-list', '#tr-detail'].map("
            "s => document.querySelector(s).getBoundingClientRect().bottom)"
        )
        assert all(900 - 24 <= bottom <= 900 for bottom in bottoms)
        # 단축키 안내는 제목 줄이 아니라 상세 패널 행동 줄에 있다
        assert page.locator(".page-head .tr-keys").count() == 0
        assert page.locator("#tr-detail .tr-actions .tr-keys").count() == 1

        page.set_viewport_size({"width": 1100, "height": 900})
        narrow = _open(page, base, "/suggestions")
        assert HEAD_HEIGHT < narrow["headHeight"] <= 2 * HEAD_HEIGHT + 12  # 2줄까지
        assert narrow["scrollX"] <= 0
