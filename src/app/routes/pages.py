"""화면 라우터(서버 렌더링)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.deps import get_container
from app.templating import render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request) -> HTMLResponse:
    container = get_container(request)
    return render(
        request,
        "dashboard.html",
        "dashboard",
        collector_label=container.mail_detection.message,
        retention_days=container.settings.retention_days,
    )


@router.get("/suggestions", response_class=HTMLResponse)
def suggestions_page(request: Request) -> HTMLResponse:
    return render(request, "suggestions.html", "suggestions")


@router.get("/weekly", response_class=HTMLResponse)
def weekly_page(request: Request) -> HTMLResponse:
    return render(request, "weekly.html", "weekly")


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request, q: str = "") -> HTMLResponse:
    return render(request, "history.html", "history", initial_query=q[:200])


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    container = get_container(request)
    return render(
        request,
        "settings.html",
        "settings",
        collector_label=container.mail_detection.message,
        exclusion_summary=container.mail.rules.summary(),
        retention_days=container.settings.retention_days,
    )
