"""주간보고 API(WIKI-01~06): 초안 → 편집 → 미리보기 → 승인 → 게시."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from domain.calendar import week_start
from domain.clock import KST
from domain.errors import UserInputError
from domain.weekly.report import MAX_ITEM_CHARS, MAX_ITEMS, OwnSectionError, WeeklyDraft
from domain.weekly.service import WikiConflictError, WikiUnavailableError
from security.links import safe_link
from security.logging_policy import EventType, Site, log_event

router = APIRouter(prefix="/api/weekly")

Item = Annotated[str, Field(min_length=1, max_length=MAX_ITEM_CHARS)]


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: datetime
    done: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    planned: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    issues: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)

    def to_draft(self) -> WeeklyDraft:
        if self.week_start.tzinfo is None or week_start(self.week_start) != self.week_start:
            raise UserInputError("주 시작 시각이 올바르지 않습니다")
        return WeeklyDraft(self.week_start, list(self.done), list(self.planned), list(self.issues))


class PublishBody(DraftBody):
    nonce: str = Field(min_length=16, max_length=64)  # 미리보기 때 서버가 발급한 1회용 번호
    mode: Literal["create", "update"]
    approved: Literal[True]  # 사용자가 '승인 후 게시'를 눌렀을 때만 True


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _draft_json(draft: WeeklyDraft) -> dict[str, object]:
    return {
        "week_start": draft.week_start.astimezone(KST).isoformat(),
        "done": draft.done,
        "planned": draft.planned,
        "issues": draft.issues,
    }


@router.post("/draft")
def make_draft(request: Request) -> JSONResponse:
    return JSONResponse(_draft_json(get_container(request).weekly.draft()))


@router.post("/preview")
def preview(request: Request, body: DraftBody) -> JSONResponse:
    try:
        result = get_container(request).weekly.preview(body.to_draft())
    except UserInputError as exc:
        return _error(str(exc), 400)
    return JSONResponse(
        {
            "title": result.title,
            "section_html": result.section_html,
            "nonce": result.nonce,
            "base_version": result.base_version,
            "current_section": result.current_section,
        }
    )


@router.post("/publish")
def publish(request: Request, body: PublishBody) -> JSONResponse:
    weekly = get_container(request).weekly
    try:
        approved = weekly.approve(body.to_draft(), body.nonce, body.mode)
        page = weekly.publish(approved)
    except (WikiConflictError, OwnSectionError) as exc:
        log_event(EventType.WIKI_POST, Site.WIKI, success=False)
        return _error(str(exc), 409)
    except WikiUnavailableError as exc:
        log_event(EventType.WIKI_POST, Site.WIKI, success=False)
        return _error(str(exc), 503)
    except UserInputError as exc:
        return _error(str(exc), 400)
    log_event(EventType.WIKI_POST, Site.WIKI, success=True)
    link = safe_link(page.link, [str(get_container(request).settings.wiki_base_url)])
    return JSONResponse({"title": page.title, "version": page.version, "link": link})


@router.get("/history")
def history(request: Request) -> JSONResponse:
    container = get_container(request)
    records = container.publish_log.list_recent()
    wiki_base = [str(container.settings.wiki_base_url)]
    return JSONResponse(
        [
            {
                "week_start": r.week_start.astimezone(KST).isoformat(),
                "published_at": r.published_at.astimezone(KST).isoformat(),
                "version": r.page_version,
                "link": safe_link(r.page_link or "", wiki_base),
            }
            for r in records
        ]
    )
