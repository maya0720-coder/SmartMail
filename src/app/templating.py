"""서버 렌더링 템플릿. 자동 이스케이프를 항상 켠다(XSS 방지)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Final

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.deps import get_container

TEMPLATE_DIR: Final = Path(__file__).resolve().parent / "templates"
STATIC_DIR: Final = Path(__file__).resolve().parent / "static"

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

SITE_LABELS: Final = {
    "wiki": "위키",
    "g-knowledge": "G-Knowledge",
    "ai-ground": "AI-Ground",
    "groupware": "그룹웨어",
}

NAV_ITEMS: Final = (
    ("dashboard", "/", "대시보드"),
    ("suggestions", "/suggestions", "제안된 업무"),
    ("weekly", "/weekly", "주간보고"),
    ("history", "/history", "업무 히스토리"),
    ("settings", "/settings", "설정·연결"),
)


_ASSET_HASHES: dict[tuple[str, float], str] = {}


def asset(path: str) -> str:
    """정적 파일 주소에 내용 해시를 붙인다(`/static/js/a.js?v=…`).

    파일이 바뀌면 주소도 바뀌므로, 브라우저에 남은 예전 JS·CSS가 새 화면과 섞이지 않는다.
    템플릿에서 상수 경로로만 부르며, 정적 폴더 밖 경로는 거부한다.
    """
    target = (STATIC_DIR / path).resolve()
    if STATIC_DIR.resolve() not in target.parents or not target.is_file():
        raise ValueError("정적 파일 경로가 올바르지 않습니다")
    key = (path, target.stat().st_mtime)
    if key not in _ASSET_HASHES:
        _ASSET_HASHES[key] = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
    return f"/static/{path}?v={_ASSET_HASHES[key]}"


_env.globals["asset"] = asset


def render(request: Request, template: str, active: str, **context: Any) -> HTMLResponse:
    container = get_container(request)
    html = _env.get_template(template).render(
        csrf_token=container.csrf_token,
        demo_mode=container.settings.demo_mode,
        nav_items=NAV_ITEMS,
        active=active,
        site_labels=SITE_LABELS,
        **context,
    )
    return HTMLResponse(html, headers={"cache-control": "no-store"})
