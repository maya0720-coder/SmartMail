"""실제 브라우저(Playwright, 비저장 컨텍스트)로 화면을 점검한다(12단계).

로컬 127.0.0.1에 데모 모드 서버를 띄우고, 외부 사이트에는 접속하지 않는다.
화면 캡처는 테스트 임시 폴더에만 저장한다.
Chromium이 설치되어 있지 않으면 건너뛴다.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from app.factory import create_app
from domain.clock import SystemClock
from fixtures.app_factory import make_settings

sync_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, object]]:
    from app.bootstrap import build_container
    from fixtures.app_factory import FakeBackend
    from security.browser import BrowserRuntime

    port = _free_port()
    settings = make_settings(tmp_path, app_port=port)
    container = build_container(
        settings, clock=SystemClock(), browser=BrowserRuntime(backend_factory=FakeBackend)
    )
    app = create_app(container, run_background_jobs=False)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, server_header=False, lifespan="on"
    )
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not instance.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert instance.started
    yield f"http://127.0.0.1:{port}", container
    instance.should_exit = True
    thread.join(timeout=10)


def _dismiss_login(page: object, base: str) -> None:
    """시작 로그인 창을 '나중에'로 닫은 상태로 만든다(이번 실행 동안 자동으로 뜨지 않음)."""
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")  # type: ignore[attr-defined]
    response = page.request.post(  # type: ignore[attr-defined]
        base + "/api/login/later",
        data={},
        headers={"X-CSRF-Token": token or "", "Content-Type": "application/json"},
    )
    assert response.ok


@pytest.mark.slow
def test_dashboard_grain_switch_and_xss(server: tuple[str, object], tmp_path: Path) -> None:
    base, _container = server
    try:
        playwright = sync_api.sync_playwright().start()
    except Exception as exc:  # pragma: no cover - 환경 의존
        pytest.skip(f"Playwright 시작 불가: {type(exc).__name__}")
    try:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - 브라우저 미설치
            pytest.skip(f"Chromium 실행 불가: {type(exc).__name__}")
        context = browser.new_context()  # 비저장 컨텍스트
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

        page.goto(base + "/")
        _dismiss_login(page, base)
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        xss = '<img src=x onerror="window.__xss=1"> 테스트 업무'
        response = page.request.post(
            base + "/api/tasks",
            data={"title": xss},
            headers={"X-CSRF-Token": token or "", "Content-Type": "application/json"},
        )
        assert response.ok
        page.reload()
        page.wait_for_selector("#todo tr")
        for grain in ("year", "half", "quarter", "month", "week"):
            page.click(f'#seg button[data-g="{grain}"]')
            page.wait_for_timeout(150)
            assert page.locator("#mk .kpi").count() == 4
        assert page.evaluate("window.__xss") is None
        assert page.locator("#todo", has_text="테스트 업무").count() == 1
        for path in ("/suggestions", "/weekly", "/history", "/settings"):
            page.goto(base + path)
            page.wait_for_timeout(200)
        page.screenshot(path=str(tmp_path / "dashboard.png"))  # 로컬 임시 폴더에만 저장
        context.close()
        browser.close()
        assert errors == []
    finally:
        playwright.stop()


# ---- 디자인 명세 01(ui-ux-pro-max) 7장 추가 검증: 페이징·정렬·외부 리소스 ----


def _browser_or_skip() -> tuple[object, object]:
    try:
        playwright = sync_api.sync_playwright().start()
    except Exception as exc:  # pragma: no cover - 환경 의존
        pytest.skip(f"Playwright 시작 불가: {type(exc).__name__}")
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception as exc:  # pragma: no cover - 브라우저 미설치
        playwright.stop()
        pytest.skip(f"Chromium 실행 불가: {type(exc).__name__}")
    return playwright, browser


def _seed_tasks(page: object, base: str, count: int) -> None:
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")  # type: ignore[attr-defined]
    headers = {"X-CSRF-Token": token or "", "Content-Type": "application/json"}
    for index in range(count):
        response = page.request.post(  # type: ignore[attr-defined]
            base + "/api/tasks", data={"title": f"합성 업무 {index:02d}"}, headers=headers
        )
        assert response.ok


def _info(page: object) -> tuple[int, int, int]:
    """'1–7 / 40건 · 페이지당 7건' → (시작, 끝, 전체)"""
    text = page.inner_text("#todo-info").split(" · ")[0]  # type: ignore[attr-defined]
    span, total = text.replace("건", "").split(" / ")
    start, end = span.split("–")
    return int(start), int(end), int(total)


@pytest.mark.slow
@pytest.mark.parametrize("viewport", [(1440, 900), (1024, 768), (390, 844)])
def test_ui_todo_paging_fits_viewport(
    server: tuple[str, object], viewport: tuple[int, int]
) -> None:
    base, _container = server
    playwright, browser = _browser_or_skip()
    try:
        context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})  # type: ignore[attr-defined]
        page = context.new_page()
        external: list[str] = []
        page.on("request", lambda r: external.append(r.url) if not r.url.startswith(base) else None)
        page.goto(base + "/")
        _dismiss_login(page, base)
        _seed_tasks(page, base, 40)
        page.reload()
        page.wait_for_selector("#todo tr.row")
        page.wait_for_timeout(300)
        start, end, total = _info(page)
        rows = page.locator("#todo tr.row").count()
        assert start == 1 and rows == end and total >= 40
        assert 5 <= rows < total  # 화면 높이에 맞춘 페이지 크기(최소 5행)
        # 표 아래쪽이 창 높이를 넘지 않는다(모바일은 카드형이라 제외)
        if viewport[0] > 720:
            bottom = page.evaluate("document.querySelector('#todo').getBoundingClientRect().bottom")
            top = page.evaluate("document.querySelector('#todo').getBoundingClientRect().top")
            assert bottom - top <= viewport[1]
        # 다음·이전·번호
        page.click('#todo-pager button[aria-label="다음 페이지"]')
        assert _info(page)[0] == rows + 1
        page.click('#todo-pager button[aria-label="이전 페이지"]')
        assert _info(page)[0] == 1
        page.click('#todo-pager button[aria-label="2페이지"]')
        assert page.locator('#todo-pager [aria-current="page"]').inner_text() == "2"
        # 필터를 바꾸면 1페이지
        page.click('#filters button[data-f="all"]')
        assert _info(page)[0] == 1
        # 폰트·아이콘을 포함한 모든 요청이 같은 출처
        assert external == []
        context.close()
    finally:
        browser.close()  # type: ignore[attr-defined]
        playwright.stop()  # type: ignore[attr-defined]


@pytest.mark.slow
def test_ui_dashboard_app_shell_layout(server: tuple[str, object]) -> None:
    """디자인 명세 03, 6장: 1440×900 한 화면 고정형, 상세 320px·목록과 같은 높이, KPI ≤ 100px."""
    base, _container = server
    playwright, browser = _browser_or_skip()
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 900})  # type: ignore[attr-defined]
        page.goto(base + "/")
        _dismiss_login(page, base)
        _seed_tasks(page, base, 30)
        page.reload()
        page.wait_for_selector("#todo tr.row")
        page.wait_for_timeout(300)
        rect = (
            "(s) => { const r = document.querySelector(s).getBoundingClientRect();"
            " return [r.y, r.width, r.height, r.bottom]; }"
        )
        assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")
        # 아래 여백 16px(--page-bottom, 결정기록 0016) + 페이저 줄
        assert page.evaluate(rect, "#todo-wrap")[3] >= 900 - 64
        detail, card = page.evaluate(rect, "#detail"), page.evaluate(rect, ".todo-card")
        assert abs(detail[1] - 320) <= 4 and abs(detail[2] - card[2]) <= 1
        heights = page.evaluate(
            "[...document.querySelectorAll('#mk .kpi')].map(e => e.getBoundingClientRect().height)"
        )
        assert len(heights) == 4 and max(heights) <= 100
        # 명세 03의 300px에서 완화: 명세 06의 공통 여백(14·36·12·12)으로 머리 영역이 20px 늘었다
        assert page.evaluate(rect, "#todo tr.row")[0] <= 320
        assert page.locator("#status-donut .lg-item").count() == 6  # 범례 6개 항상 표시
    finally:
        browser.close()  # type: ignore[attr-defined]
        playwright.stop()  # type: ignore[attr-defined]


@pytest.mark.slow
def test_ui_mail_original_in_detail_center(server: tuple[str, object]) -> None:
    """원본 보기(MAIL-08~10, 결정기록 0022).

    원본 메일은 상세 패널 가운데(작업 버튼 행과 하단 상태 버튼 사이)에 보이고,
    목록 아래 메일 영역은 없다. '원본 보기'로 숨기기/보이기를 바꾼다.
    """
    base, _container = server
    playwright, browser = _browser_or_skip()
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 900})  # type: ignore[attr-defined]
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + "/")
        _dismiss_login(page, base)
        token = page.locator('meta[name="csrf-token"]').get_attribute("content")
        headers = {"X-CSRF-Token": token or "", "Content-Type": "application/json"}
        collected = page.request.post(
            base + "/api/mail/collect", data={"period": "sync"}, headers=headers
        )
        assert collected.ok  # 데모 메일 수집(가상 데이터) → 원본이 있는 업무 생성
        page.reload()
        page.wait_for_selector("#todo tr.row")
        assert page.locator("#mail-pane").count() == 0  # 좌측 하단 메일 박스 제거
        page.locator("#todo tr.row").first.click()
        page.wait_for_selector("#d-body .d-tools")
        # 업무를 고르면 팝업 없이 상세 가운데 영역에 발신자·수신 일시·본문이 보인다
        page.wait_for_selector("#detail #d-mail:not(.hidden) .dm-body .mail-full")
        assert page.locator("#d-mail .dm-meta dt", has_text="보낸 사람").count() == 1
        assert page.locator("#d-mail .dm-meta dt", has_text="수신 일시").count() == 1
        assert page.locator(".dim.show").count() == 0
        # 배치: 작업 버튼 행 아래, 상세 본문(#d-body) 안 = 하단 상태 버튼(#d-foot) 위.
        # 남은 높이가 작으면 상세 본문이 스크롤되므로 메일 끝이 아니라 본문 영역 끝을 비교한다.
        tools_box = page.locator("#d-body .d-tools").bounding_box()
        mail_box = page.locator("#d-mail").bounding_box()
        body_box = page.locator("#d-body").bounding_box()
        foot_box = page.locator("#d-foot").bounding_box()
        assert tools_box and mail_box and body_box and foot_box
        assert tools_box["y"] + tools_box["height"] <= mail_box["y"]
        assert page.locator("#d-body > #d-mail").count() == 1
        assert body_box["y"] + body_box["height"] <= foot_box["y"] + 1
        # 본문은 영역 안에서 스크롤한다
        overflow = page.eval_on_selector("#d-mail .dm-body", "e => getComputedStyle(e).overflowY")
        assert overflow == "auto"
        # '원본 보기' 버튼이 가운데 영역 보이기/숨기기와 연동된다
        toggle = page.locator(".d-tools button[aria-controls='d-mail']")
        assert toggle.get_attribute("aria-pressed") == "true"
        toggle.click()
        page.wait_for_selector("#d-mail.hidden", state="attached")
        assert toggle.get_attribute("aria-pressed") == "false"
        assert "원본 보기" in toggle.inner_text()
        toggle.click()
        page.wait_for_selector("#d-mail:not(.hidden) .dm-body .mail-full")
        assert toggle.get_attribute("aria-pressed") == "true"
        assert errors == []
    finally:
        browser.close()  # type: ignore[attr-defined]
        playwright.stop()  # type: ignore[attr-defined]


@pytest.mark.slow
def test_ui_status_and_category_badges_distinct(server: tuple[str, object]) -> None:
    """상태·업무 구분 배지 색(결정기록 0022): 상태마다, 구분마다 색이 서로 다르다."""
    base, _container = server
    playwright, browser = _browser_or_skip()
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 900})  # type: ignore[attr-defined]
        page.goto(base + "/")
        _dismiss_login(page, base)
        page.wait_for_selector("#todo")
        colors = page.evaluate(
            """() => {
              const probe = (cls) => {
                const n = document.createElement('span');
                n.className = cls;
                n.textContent = 'x';
                document.body.append(n);
                const s = getComputedStyle(n);
                const dot = getComputedStyle(n, '::before').backgroundColor;
                const out = [s.backgroundColor, s.color, dot].join('|');
                n.remove();
                return out;
              };
              const st = ['b-prop','b-wait','b-prog','b-cand','b-done','b-hold']
                .map(c => probe('badge ' + c));
              const cat = ['cat-tag cat-appr','cat-tag cat-gen'].map(probe);
              return { st, cat };
            }"""
        )
        assert len(set(colors["st"])) == 6
        assert len(set(colors["cat"])) == 2
        assert not set(colors["cat"]) & set(colors["st"])
    finally:
        browser.close()  # type: ignore[attr-defined]
        playwright.stop()  # type: ignore[attr-defined]


@pytest.mark.slow
def test_ui_drawer_bulk_undo_and_search(server: tuple[str, object]) -> None:
    """서랍(1024px)·일괄 작업·되돌리기·검색 거르기(디자인 명세 03, 4장)."""
    base, _container = server
    playwright, browser = _browser_or_skip()
    try:
        page = browser.new_page(viewport={"width": 1024, "height": 768})  # type: ignore[attr-defined]
        page.goto(base + "/")
        _dismiss_login(page, base)
        _seed_tasks(page, base, 6)
        page.reload()
        page.wait_for_selector("#todo tr.row")
        # 행을 누르면 서랍이 열리고, Esc로 닫으면 포커스가 행으로 돌아온다
        page.locator("#todo tr.row").first.click()
        page.wait_for_selector("body.det-open")
        assert page.get_attribute("#detail", "aria-modal") == "true"
        page.keyboard.press("Escape")
        assert not page.evaluate("document.body.classList.contains('det-open')")
        assert page.evaluate("document.activeElement.matches('#todo tr.row')")
        # 2건 체크 → 진행 시작 → 성공 집계
        boxes = page.locator("#todo tr.row .cb")
        boxes.nth(0).check()
        boxes.nth(1).check()
        page.locator("#bulk button", has_text="진행 시작").click()
        # CSP가 eval을 막으므로 문자열 식 대신 로케이터로 기다린다
        page.locator("#toast", has_text="성공 2건").wait_for()
        # 단건 변경 후 되돌리기
        page.locator("#todo tr.row").first.click()
        page.wait_for_selector("body.det-open")
        page.locator("#d-foot button", has_text="보류").click()
        page.wait_for_selector("#toast .toast-act")
        page.click("#toast .toast-act")
        page.locator("#toast", has_text="되돌렸습니다").wait_for()
        page.keyboard.press("Escape")
        # 상단 검색은 대시보드 목록을 바로 거르고, Esc로 지운다
        page.fill("#search-form input", "없는검색어zz")
        page.locator("#todo", has_text="검색 결과가 없습니다").wait_for()
        page.keyboard.press("Escape")
        page.wait_for_selector("#todo tr.row")
        assert page.input_value("#search-form input") == ""
    finally:
        browser.close()  # type: ignore[attr-defined]
        playwright.stop()  # type: ignore[attr-defined]


@pytest.mark.slow
def test_ui_todo_sort_cycle_and_keyboard(server: tuple[str, object]) -> None:
    base, _container = server
    playwright, browser = _browser_or_skip()
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 900})  # type: ignore[attr-defined]
        page.goto(base + "/")
        _dismiss_login(page, base)
        _seed_tasks(page, base, 12)
        page.reload()
        page.wait_for_selector("#todo tr.row")
        head = '#todo-head th[data-sort="title"]'
        # 기본 정렬은 최근 생성순: 마지막에 만든 업무가 맨 위
        assert "합성 업무 11" in page.locator("#todo tr.row").first.inner_text()
        assert page.get_attribute(head, "aria-sort") == "none"
        page.click(head + " button")
        assert page.get_attribute(head, "aria-sort") == "ascending"
        page.click(head + " button")
        assert page.get_attribute(head, "aria-sort") == "descending"
        page.focus(head + " button")
        page.keyboard.press("Enter")  # 키보드로도 정렬 해제
        assert page.get_attribute(head, "aria-sort") == "none"
        # 목록에 포커스가 있을 때 → 키로 다음 페이지
        if page.locator('#todo-pager button[aria-label="다음 페이지"]').count():
            page.focus("#todo-wrap")
            page.keyboard.press("ArrowRight")
            assert page.locator('#todo-pager [aria-current="page"]').inner_text() == "2"
    finally:
        browser.close()  # type: ignore[attr-defined]
        playwright.stop()  # type: ignore[attr-defined]


@pytest.mark.slow
def test_ui_startup_login_modal(server: tuple[str, object]) -> None:
    """앱을 열면 시작 로그인 창이 자동으로 뜨고, ID 1개 + 암호로 로그인한다(결정기록 0014)."""
    base, _container = server
    playwright, browser = _browser_or_skip()
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 900})  # type: ignore[attr-defined]
        page.goto(base + "/")
        page.wait_for_selector("#dlg-start.show")
        assert page.inner_text("#st-suffix") == "@golfzon.com"
        # AI-Ground·G-Knowledge·위키. 그룹웨어는 일괄 로그인에서 뺐다(결정기록 0026)
        assert page.locator("#st-sites li").count() == 3
        assert "그룹웨어" not in page.inner_text("#st-sites")
        page.fill("#st-id", "tester01")
        page.fill("#st-pw", "x" * 12)  # 합성 값(데모: 가짜 로그인)
        page.click("#st-submit")
        page.wait_for_selector("#dlg-start.show", state="hidden")
        assert page.input_value("#st-pw") == "" and page.input_value("#st-id") == ""
        assert page.locator("#conn .chip.on").count() == 3  # 그룹웨어는 아직 로그인 안 됨
        # 그룹웨어는 설정·연결에서 직접 로그인한다(그 사이트만 묻는 창)
        page.goto(base + "/settings")
        page.locator(".set-row", has_text="그룹웨어").locator("button", has_text="로그인").click()
        page.wait_for_selector("#dlg-start.show")
        assert "그룹웨어 로그인" in page.inner_text("#st-title")
        assert page.locator("#st-sites li").count() == 1
        page.fill("#st-id", "tester01")
        page.fill("#st-pw", "x" * 12)
        page.click("#st-submit")
        page.wait_for_selector("#dlg-start.show", state="hidden")
        assert page.locator("#conn .chip.on").count() == 4
        page.goto(base + "/")
        # 상태 칩을 누르면 사이트를 새 탭으로 연다(https 주소, 결정기록 0017)
        chip = page.locator("#conn a.chip").first
        assert (chip.get_attribute("href") or "").startswith("https://")
        assert chip.get_attribute("target") == "_blank"
        assert chip.get_attribute("rel") == "noopener noreferrer"
        # 설정 화면에는 로그인 입력 칸이 없다(시작 로그인 창으로 통일)
        page.goto(base + "/settings")
        assert page.locator("#login-all-form").count() == 0
    finally:
        browser.close()  # type: ignore[attr-defined]
        playwright.stop()  # type: ignore[attr-defined]


@pytest.mark.slow
def test_ui_startup_login_later(server: tuple[str, object]) -> None:
    base, _container = server
    playwright, browser = _browser_or_skip()
    try:
        page = browser.new_page()  # type: ignore[attr-defined]
        page.goto(base + "/")
        page.wait_for_selector("#dlg-start.show")
        page.click("#st-later")
        page.wait_for_selector("#dlg-start.show", state="hidden")
        page.goto(base + "/suggestions")  # 이번 실행 동안 다시 뜨지 않음
        page.wait_for_timeout(500)
        assert page.locator("#dlg-start.show").count() == 0
    finally:
        browser.close()  # type: ignore[attr-defined]
        playwright.stop()  # type: ignore[attr-defined]
