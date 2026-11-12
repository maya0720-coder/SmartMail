"""위키 주간보고 제목 규칙 조회·변경 API(WIKI-02)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from app.title_rule import save_rule
from domain.calendar import week_start
from domain.errors import UserInputError
from domain.weekly.title_rule import MAX_FORMAT_CHARS, MAX_OFFSET_DAYS, OFFSET_WEEKDAY_LABELS

router = APIRouter(prefix="/api/weekly/title-rule")


class TitleRuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_title_format: str = Field(min_length=1, max_length=MAX_FORMAT_CHARS)
    offset_days: int = Field(ge=0, le=MAX_OFFSET_DAYS)


def _rule_json(request: Request) -> dict[str, object]:
    container = get_container(request)
    template = container.weekly.template
    return {
        "page_title_format": template.page_title_format,
        "offset_days": template.title_date_offset_days,
        "weekday_labels": list(OFFSET_WEEKDAY_LABELS),
        "example": template.page_title(week_start(container.clock.now())),
    }


@router.get("")
def get_rule(request: Request) -> JSONResponse:
    return JSONResponse(_rule_json(request))


@router.put("")
def put_rule(request: Request, body: TitleRuleBody) -> JSONResponse:
    container = get_container(request)
    try:
        container.weekly.template = save_rule(
            container.weekly.template,
            container.app_settings,
            body.page_title_format,
            body.offset_days,
        )
    except UserInputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(_rule_json(request))
