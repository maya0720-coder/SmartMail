"""G-Knowledge 등록 미리보기·등록 API(GK-01·02·06·07).

등록은 백그라운드 큐에 넣고 바로 202로 응답한다. 진행은 상태 API로 본다(결정기록 0035).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from app.gk_job import RetryConfirmRequiredError
from app.routes.tasks import task_json
from domain.errors import UserInputError
from domain.knowledge import (
    MAX_BODY,
    MAX_TAGS,
    MAX_TITLE,
    DuplicateRegistrationError,
    draft_from_edit,
)
from domain.tasks.service import TaskNotFoundError

router = APIRouter(prefix="/api/gk")
MAX_STATUS_IDS = 100


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    body: str = Field(min_length=1, max_length=MAX_BODY)
    tags: list[Annotated[str, Field(max_length=30)]] = Field(
        default_factory=list, max_length=MAX_TAGS
    )
    # 결과 미확인(unknown) 건을 다시 보낼 때: 중복 경고를 확인했다는 표시
    retry_unknown: bool = False


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
    """검증·재마스킹 후 백그라운드 큐에 넣는다. 결과를 기다리지 않는다(202)."""
    container = get_container(request)
    try:
        draft = draft_from_edit(body.title, body.body, body.tags)  # 사용자 수정값 재마스킹
        status = container.gk_queue.enqueue(task_id, draft, retry_unknown=body.retry_unknown)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    except RetryConfirmRequiredError as exc:
        return JSONResponse({"error": str(exc), "code": "confirm_required"}, status_code=409)
    except DuplicateRegistrationError as exc:
        return _error(str(exc), 409)
    except UserInputError as exc:
        return _error(str(exc), 400)
    return JSONResponse(status.as_json(), status_code=202)


@router.get("/status")
def status(request: Request, ids: str = Query(default="", max_length=1000)) -> JSONResponse:
    """업무별 등록 상태. ids는 쉼표로 구분한 업무 번호(최대 100개)."""
    parts = [p for p in ids.split(",") if p]
    if len(parts) > MAX_STATUS_IDS or not all(p.isascii() and p.isdigit() for p in parts):
        return _error("업무 번호 형식을 확인해 주세요.", 400)
    found = get_container(request).gk_queue.statuses([int(p) for p in parts])
    return JSONResponse({str(k): v.as_json() for k, v in found.items()})


@router.post("/mark-registered/{task_id}")
def mark_registered(request: Request, task_id: int) -> JSONResponse:
    """결과 미확인 건을 사용자가 G-Knowledge에서 확인한 뒤 '등록됨'으로 표시한다."""
    container = get_container(request)
    try:
        container.gk_queue.mark_registered(task_id)
        task = container.tasks.get(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    except DuplicateRegistrationError as exc:
        return _error(str(exc), 409)
    except ValueError as exc:
        return _error(str(exc), 409)
    return JSONResponse(task_json(request, task))
