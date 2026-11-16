"""필요할 때 일괄 로그인 화면 테스트(N3, AUTH-01·05·09, 결정기록 0034).

로컬 127.0.0.1 데모 서버(가짜 어댑터)만 쓴다. 데모는 로그인 없이도 동작하므로,
실제 어댑터를 쓰는 실행처럼 보이게 login_free_sites를 비워 로그인이 필요하도록 만든다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fixtures.ui_server import chromium, csrf_post, demo_server


def _track(page: Any, path: str) -> list[str]:
    calls: list[str] = []
    page.on("request", lambda req: calls.append(req.url) if req.url.endswith(path) else None)
    return calls


def _open(page: Any, base: str) -> None:
    page.goto(base + "/")
    csrf_post(page, base, "/api/mail/collect")  # 가상 메일 수집(요약 대기 업무가 생김)
    page.goto(base + "/suggestions")
    page.wait_for_selector("#tr-items .tr-item")
    page.wait_for_selector("#sum-run:not([disabled])")


def _login(page: Any) -> None:
    page.fill("#st-id", "tester01")
    page.fill("#st-pw", "x" * 12)  # 합성 값(데모: 가짜 로그인)
    page.click("#st-submit")
    page.wait_for_selector("#dlg-start.show", state="hidden")


@pytest.mark.slow
def test_ui_auth01_bulk_summary_asks_login_then_resumes(tmp_path: Path) -> None:
    """미로그인에서 [AI로 일괄 요약]을 누르면 창이 뜬다.

    취소하면 호출하지 않고, 로그인하면 원래 작업을 이어서 실행한다.
    """
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        container.login_free_sites = frozenset()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        runs = _track(page, "/api/summaries/run")
        _open(page, base)
        page.wait_for_timeout(400)
        assert page.locator("#dlg-start.show").count() == 0  # 시작 시 자동으로 뜨지 않음

        page.click("#sum-run")
        page.wait_for_selector("#dlg-start.show")
        assert "AI 일괄 요약" in page.inner_text("#st-title")
        assert "AI-Ground" in page.inner_text("#st-sub")
        page.keyboard.press("Escape")  # 취소
        page.wait_for_selector("#dlg-start.show", state="hidden")
        page.wait_for_selector("#toast.show")
        assert "로그인 후 다시 시도" in page.inner_text("#toast")
        assert runs == []

        page.click("#sum-run")
        page.wait_for_selector("#dlg-start.show")
        page.uncheck("#st-also")  # 이번 작업에 필요한 사이트만
        _login(page)
        page.wait_for_timeout(500)
        assert len(runs) == 1  # 로그인 후 원래 작업을 자동으로 이어서 실행
        chips = page.locator("#conn .chip.on")
        assert chips.count() == 1 and "AI-Ground" in chips.inner_text()

        # 이미 로그인되어 있으면 창 없이 바로 실행한다
        page.wait_for_timeout(1200)
        if page.locator("#sum-run").is_enabled():
            page.click("#sum-run")
            page.wait_for_timeout(500)
            assert page.locator("#dlg-start.show").count() == 0
            assert len(runs) == 2
        assert errors == []


@pytest.mark.slow
def test_ui_auth05_company_sso_without_window(tmp_path: Path) -> None:
    """회사 계정이 연결돼 있으면 다른 회사 계정 사이트는 먼저 입력 없이 SSO를 시도한다.

    데모 가짜 인증기는 SSO를 지원하지 않으므로, 시도 후 그 사이트만 묻는 창이 열리고
    로그인하면 원래 작업이 이어서 실행된다.
    """
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        container.login_free_sites = frozenset()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _open(page, base)
        page.click("#sum-run")
        page.wait_for_selector("#dlg-start.show")
        page.uncheck("#st-also")
        _login(page)  # AI-Ground만 로그인
        bodies: list[str] = []
        page.on(
            "request",
            lambda req: (
                bodies.append(req.post_data or "") if req.url.endswith("/login/all") else None
            ),
        )
        page.evaluate(
            "() => { window.__ran = 0;"
            " ensureLogin(['g-knowledge'], () => { window.__ran += 1; },"
            " { label: '유사 이력 찾기' }); }"
        )
        page.wait_for_selector("#dlg-start.show")
        # 입력 없는 SSO 시도가 먼저 갔다(자격증명 없음)
        assert bodies and '"sites":["g-knowledge"]' in bodies[0] and "password" not in bodies[0]
        assert "유사 이력 찾기" in page.inner_text("#st-title")
        assert page.locator("#st-sites li").count() == 2  # G-Knowledge + (함께) 위키
        assert page.evaluate("() => window.__ran") == 0
        _login(page)
        for _ in range(50):  # CSP가 문자열 평가(wait_for_function)를 막으므로 짧게 반복 확인
            if page.evaluate("() => window.__ran") == 1:
                break
            page.wait_for_timeout(100)
        assert page.evaluate("() => window.__ran") == 1
        page.wait_for_selector("#conn a.chip.on >> nth=2")  # AI-Ground·G-Knowledge·위키
