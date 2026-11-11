"""화면 렌더링·XSS·CSP 준수 테스트(12단계). 합성 데이터만 쓴다."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.templating import STATIC_DIR, TEMPLATE_DIR
from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container

PAGES = ["/", "/suggestions", "/weekly", "/history", "/settings"]


@pytest.mark.parametrize("path", PAGES)
def test_pages_render(tmp_path: Path, clock: FixedClock, path: str) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert 'name="csrf-token"' in response.text
    assert "데모 모드" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_query_is_escaped_in_history_page(tmp_path: Path, clock: FixedClock) -> None:
    payload = '"><script>alert(1)</script>'
    with app_client(make_container(tmp_path, clock)) as client:
        html = client.get("/history", params={"q": payload}).text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_templates_have_no_inline_script_style_or_handlers() -> None:
    """CSP(script-src·style-src 'self') 준수: 인라인 스크립트·스타일·이벤트 속성 금지."""
    offenders = []
    for path in TEMPLATE_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text):
            offenders.append(f"{path.name}: 인라인 script")
        if re.search(r"\sstyle\s*=", text) or "<style" in text:
            offenders.append(f"{path.name}: 인라인 style")
        if re.search(r"\son[a-z]+\s*=", text):
            offenders.append(f"{path.name}: 이벤트 속성")
    assert offenders == []


def test_scripts_do_not_use_unsafe_sinks_or_storage() -> None:
    """innerHTML 등 위험한 출력과 브라우저 저장소(localStorage 등) 사용 금지(12단계 작업 4)."""
    forbidden = re.compile(
        r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function"
        r"|localStorage|sessionStorage|indexedDB|document\.cookie"
    )
    offenders = [
        p.name for p in (STATIC_DIR / "js").glob("*.js") if forbidden.search(p.read_text("utf-8"))
    ]
    assert offenders == []


def test_no_external_resources() -> None:
    """외부 CDN 미사용(로컬 번들만)."""
    pattern = re.compile(r"(?:src|href)\s*=\s*[\"']https?://", re.I)
    for path in [*TEMPLATE_DIR.rglob("*.html")]:
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name


def test_mail_subject_script_returned_as_json_text(tmp_path: Path, clock: FixedClock) -> None:
    title = "<script>alert(1)</script> 요청"
    with app_client(make_container(tmp_path, clock)) as client:
        created = client.post("/api/tasks", json={"title": title})
        assert created.headers["content-type"].startswith("application/json")
        assert created.headers["x-content-type-options"] == "nosniff"
        assert created.json()["title"] == title  # 화면은 textContent로만 출력
