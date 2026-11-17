"""주간보고 HTML 편집 탭·복사 형식 게시 화면 테스트(C1·C2, WIKI-02·03·05·06, 결정기록 0041).

로컬 127.0.0.1 데모 서버(가짜 위키)만 쓴다. 외부 사이트에 접속하지 않으며 메일을 보내지 않는다.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from fixtures.ui_server import chromium, demo_server

PHONE = "010-1234-5678"
CODE = (
    "<h3>HTML 실적</h3><ul><li>합성 항목</li></ul>"
    f'<p style="color: red">빨간 글자 {PHONE}</p><script>window.__x = 1</script>'
    '<img src="x" onerror="window.__y = 1">'
)


def _until(check: Any, timeout: float = 5.0) -> None:
    """CSP 때문에 wait_for_function을 못 써서 직접 기다린다."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError("시간 안에 조건을 만족하지 않았습니다")


@pytest.mark.slow
def test_ui_wiki02_html_tab_preview_and_round_trip(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + "/weekly")
        page.wait_for_selector("#wk-editor h3")
        page.click("#tab-html")
        assert page.is_visible("#wk-html") and not page.is_visible("#wk-rich")
        assert "<h3" in page.input_value("#html-code")  # 미리보기 편집 내용이 코드로
        page.fill("#html-code", CODE)
        page.wait_for_selector("#html-preview h3:has-text('HTML 실적')")
        _until(lambda: "script" in page.inner_text("#html-removed"))
        preview = page.inner_text("#html-preview")
        assert PHONE not in preview and "빨간 글자" in preview
        assert page.locator("#html-preview script, #html-preview img").count() == 0
        assert page.evaluate("() => window.__x === undefined && window.__y === undefined")
        assert "KB / 200KB" in page.inner_text("#html-size")
        assert page.inner_text("#html-gutter").splitlines()[0] == "1"
        # 미리보기 편집으로: 서버 정리 결과가 들어가고 허용 style은 남는다
        page.click("#html-to-rich")
        page.wait_for_selector("#wk-rich:not(.hidden)")
        page.wait_for_selector("#wk-editor h3:has-text('HTML 실적')")
        color = page.evaluate(
            "() => document.querySelector('#wk-editor p').style.getPropertyValue('color')"
        )
        assert color in ("red", "rgb(255, 0, 0)")
        assert PHONE not in page.inner_text("#wk-editor")
        assert page.locator("#wk-editor script, #wk-editor img").count() == 0
        # 다시 HTML로: 줄 맞춤된 코드
        page.click("#tab-html")
        assert "\n" in page.input_value("#html-code")
        assert errors == []


@pytest.mark.slow
def test_ui_wiki06_copy_format_publish_after_approval(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, _container), chromium() as browser:
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        page.on("popup", lambda popup: popup.close())  # 대상 열기 창은 닫는다(외부 접속 없음)
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(base + "/weekly")
        page.wait_for_selector("#wk-editor h3")
        page.wait_for_selector("#wk-format option[value='teams']", state="attached")
        page.select_option("#wk-format", "teams")
        assert page.inner_text("#btn-publish") == "승인 후 복사하고 Teams 열기"
        assert page.is_disabled("#btn-publish")  # 미리보기 확인(승인) 전에는 게시 불가
        # 복사 미리보기: Teams는 HTML로 보여 준다
        page.click("#btn-copy-preview")
        page.wait_for_selector("#dlg-copyprev.show")
        assert page.is_visible("#copyprev-html") and not page.is_visible("#copyprev-text")
        page.click("#copyprev-close")
        page.click("#btn-preview")
        _until(lambda: not page.is_disabled("#btn-publish"))
        page.click("#btn-publish")
        page.wait_for_selector("#dlg-wiki.show")
        assert "Teams" in page.inner_text("#wiki-head")
        assert not page.is_visible("#wiki-mode-box")  # 위키 갱신·새 페이지 선택은 위키만
        page.click("#wiki-ok")
        _until(lambda: "복사했습니다" in page.inner_text("#toast"))
        types = page.evaluate(
            "async () => (await navigator.clipboard.read()).flatMap((item) => item.types)"
        )
        assert {"text/plain", "text/html"} <= set(types)
        cache = page.request.get(base + "/api/weekly/cache").json()
        assert len(cache) == 1 and cache[0]["platform"] == "teams"
        assert errors == []
        context.close()
