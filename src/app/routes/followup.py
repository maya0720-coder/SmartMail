"""회신 대기·팔로업 API(U7, TODO-05·07, 결정기록 0038).

- `POST /api/tasks/{id}/awaiting`: 회신 대기로 표시(지금부터 근무일을 셈)
- `POST /api/tasks/{id}/awaiting/clear`: 대기 해제(같은 보낸 메일로는 다시 켜지지 않음)
- `GET  /api/tasks/{id}/followup-draft`: 팔로업 메일 초안 문안(업무 제목·경과 근무일만)
메일을 보내는 API는 없다(기획서 범위 제외). 화면은 문안을 클립보드에 복사만 한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.deps import get_container
from app.routes.tasks import _apply, _error
from domain.tasks.followup import followup_draft
from domain.tasks.service import TaskNotFoundError

router = APIRouter(prefix="/api/tasks")


@router.post("/{task_id}/awaiting")
def mark_awaiting(request: Request, task_id: int) -> JSONResponse:
    tasks = get_container(request).tasks
    return _apply(request, lambda: tasks.mark_awaiting(task_id))


@router.post("/{task_id}/awaiting/clear")
def clear_awaiting(request: Request, task_id: int) -> JSONResponse:
    tasks = get_container(request).tasks
    return _apply(request, lambda: tasks.clear_awaiting(task_id))


@router.get("/{task_id}/followup-draft")
def draft(request: Request, task_id: int) -> JSONResponse:
    container = get_container(request)
    try:
        task = container.tasks.get(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    state = container.followup.state(task, container.clock.now())
    return JSONResponse({"text": followup_draft(task.title, state.business_days)})
