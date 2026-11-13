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


def test_bundled_fonts_and_icons_are_local_and_licensed() -> None:
    """폰트·아이콘은 로컬 번들 + 라이선스 동봉(디자인 명세 01).

    스프라이트에는 스크립트·외부 참조가 없어야 한다.
    """
    fonts, img = STATIC_DIR / "fonts", STATIC_DIR / "img"
    for name in (
        "PretendardVariable.woff2",
        "FiraCode-VF.woff2",
        "LICENSE-Pretendard.txt",
        "LICENSE-FiraCode.txt",
    ):
        assert (fonts / name).is_file(), name
    assert (img / "LICENSE-Lucide.txt").is_file()
    sprite = (img / "icons.svg").read_text(encoding="utf-8")
    assert "<script" not in sprite.lower() and "http://" not in sprite.replace(
        "http://www.w3.org", ""
    )
    css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    assert not re.search(r"url\(\s*[\"']?https?://", css)  # 폰트를 외부에서 받지 않음


def test_static_assets_are_versioned_and_revalidated(tmp_path: Path, clock: FixedClock) -> None:
    """회귀: 브라우저에 남은 예전 JS가 새 화면과 섞여 대시보드가 비어 보이던 문제.

    템플릿의 스크립트·스타일·아이콘 주소는 내용 해시를 붙이고,
    정적 응답은 매번 재검증(no-cache)한다.
    """
    for path in TEMPLATE_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?:src|href)=\"/static/(?:js|css)/", text), path.name
        assert "/static/img/icons.svg" not in text, path.name
    with app_client(make_container(tmp_path, clock)) as client:
        html = client.get("/").text
        versioned = re.findall(r'src="(/static/js/common\.js\?v=[0-9a-f]{12})"', html)
        assert versioned
        response = client.get(versioned[0])
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert 'name="icon-sprite" content="/static/img/icons.svg?v=' in html


def test_asset_rejects_paths_outside_static(tmp_path: Path) -> None:
    from app.templating import asset

    for bad in ("../templating.py", "../../config/settings.py", "js/없는파일.js"):
        with pytest.raises(ValueError):
            asset(bad)
    assert asset("js/common.js").startswith("/static/js/common.js?v=")


def test_asset_version_changes_with_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.templating as templating

    static = tmp_path / "static"
    (static / "js").mkdir(parents=True)
    target = static / "js" / "a.js"
    target.write_text("1", encoding="utf-8")
    monkeypatch.setattr(templating, "STATIC_DIR", static)
    first = templating.asset("js/a.js")
    target.write_text("22", encoding="utf-8")
    import os

    os.utime(target, (target.stat().st_atime, target.stat().st_mtime + 5))
    assert templating.asset("js/a.js") != first


def test_mail_subject_script_returned_as_json_text(tmp_path: Path, clock: FixedClock) -> None:
    title = "<script>alert(1)</script> 요청"
    with app_client(make_container(tmp_path, clock)) as client:
        created = client.post("/api/tasks", json={"title": title})
        assert created.headers["content-type"].startswith("application/json")
        assert created.headers["x-content-type-options"] == "nosniff"
        assert created.json()["title"] == title  # 화면은 textContent로만 출력
