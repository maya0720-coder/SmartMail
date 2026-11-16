"""주간보고 편집 중 내용 보관 API(결정기록 0030).

- POST /api/weekly/autosave: 30초마다 서버 메모리에 보관(주차별 1개, 앱 종료 시 사라짐)
- POST /api/weekly/save-draft: [임시 저장] — 정리·마스킹한 사본을 로컬 DB에(보관 기간 후 삭제)
- GET  /api/weekly/saved-draft?week_start=…: 더 최근 보관본(없으면 빈 값)
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from domain.clock import KST
from domain.errors import UserInputError
from domain.weekly.drafts import SavedDraft
from domain.weekly.rich import MAX_HTML_BYTES

router = APIRouter(prefix="/api/weekly")
NO_STORE = {"cache-control": "no-store"}


class EditorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: datetime
    html: str = Field(max_length=MAX_HTML_BYTES)


def _json(saved: SavedDraft | None) -> JSONResponse:
    if saved is None:
        return JSONResponse({"html": None, "saved_at": None, "stored": False}, headers=NO_STORE)
    return JSONResponse(
        {
            "html": saved.html,
            "saved_at": saved.saved_at.astimezone(KST).isoformat(),
            "stored": saved.stored,
        },
        headers=NO_STORE,
    )


@router.post("/autosave")
def autosave(request: Request, body: EditorBody) -> JSONResponse:
    try:
        saved = get_container(request).weekly_drafts.autosave(body.week_start, body.html)
    except UserInputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"saved_at": saved.saved_at.astimezone(KST).isoformat()})


@router.post("/save-draft")
def save_draft(request: Request, body: EditorBody) -> JSONResponse:
    try:
        saved = get_container(request).weekly_drafts.save(body.week_start, body.html)
    except UserInputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return _json(saved)


@router.get("/saved-draft")
def saved_draft(request: Request, week_start: datetime) -> JSONResponse:
    if week_start.tzinfo is None:
        return JSONResponse({"error": "주 시작 시각이 올바르지 않습니다"}, status_code=400)
    return _json(get_container(request).weekly_drafts.load(week_start))
