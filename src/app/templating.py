"""서버 렌더링 템플릿. 자동 이스케이프를 항상 켠다(XSS 방지)."""

from __future__ import annotations

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
