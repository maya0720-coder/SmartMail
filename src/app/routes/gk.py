"""G-Knowledge 등록 미리보기·등록 API(GK-01·02·06·07)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from app.routes.tasks import task_json
from domain.errors import UserInputError
from domain.knowledge import (
    MAX_BODY,
    MAX_TAGS,
    MAX_TITLE,
    DuplicateRegistrationError,
    KnowledgeUnavailableError,
    draft_from_edit,
)
from domain.tasks.service import TaskNotFoundError
from security.logging_policy import EventType, Site, log_event

router = APIRouter(prefix="/api/gk")


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    body: str = Field(min_length=1, max_length=MAX_BODY)
    tags: list[Annotated[str, Field(max_length=30)]] = Field(
        default_factory=list, max_length=MAX_TAGS
    )


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@router.get("/preview/{task_id}")
def preview(request: Request, task_id: int) -> JSONResponse:
    container = get_container(request)
    try:
        draft = container.knowledge.preview(task_id)
        task = container.tasks.get(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    return JSONResponse(
        {
            "title": draft.title.value,
            "body": draft.body.value,
            "tags": [t.value for t in draft.tags],
            "already_registered": task.gk_registered,
            "masked_notice": "민감정보는 마스킹되었습니다. 등록 전에 내용을 확인해 주세요.",
        }
    )


@router.post("/register/{task_id}")
def register(request: Request, task_id: int, body: RegisterBody) -> JSONResponse:
    container = get_container(request)
    try:
        draft = draft_from_edit(body.title, body.body, body.tags)  # 사용자 수정값 재마스킹
        task = container.knowledge.register(task_id, draft)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    except DuplicateRegistrationError as exc:
        return _error(str(exc), 409)
    except UserInputError as exc:
        return _error(str(exc), 400)
    except KnowledgeUnavailableError as exc:
        log_event(EventType.GK_REGISTER, Site.GKNOWLEDGE, success=False)
        return _error(str(exc), 503)
    log_event(EventType.GK_REGISTER, Site.GKNOWLEDGE, success=True)
    return JSONResponse(task_json(request, task))
