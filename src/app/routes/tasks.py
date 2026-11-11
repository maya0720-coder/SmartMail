"""업무(To-do) API(TODO-02~07). 입력은 허용 목록·길이로 검증하고 대량 할당을 막는다."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from domain.clock import KST
from domain.errors import UserInputError
from domain.models import STATUS_LABELS, Task, TaskStatus
from domain.retention_rules import retention_info
from domain.tasks.service import TaskEdit, TaskNotFoundError, TransitionError, due_badge
from security.links import safe_link

router = APIRouter(prefix="/api/tasks")

ModuleTag = Literal["FI", "CO", "MM", "SD", "PP", "HR", "Basis", "ABAP"]
StatusName = Literal[
    "suggested", "waiting", "in_progress", "done_candidate", "done", "on_hold", "dismissed"
]


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    requester_dept: str | None = Field(default=None, max_length=100)
    due_date: date | None = None  # 기한 날짜(그날 23:59:59 KST)
    clear_due: bool = False
    module_tag: ModuleTag | None = None

    def to_edit(self) -> TaskEdit:
        due = None
        if self.due_date is not None:
            due = datetime.combine(self.due_date, time(23, 59, 59), tzinfo=KST)
        return TaskEdit(
            title=self.title,
            requester_dept=self.requester_dept,
            due_at=due,
            module_tag=self.module_tag,
            clear_due=self.clear_due,
        )


class ManualBody(EditBody):
    title: str = Field(min_length=1, max_length=200)


class StatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: StatusName


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(KST).isoformat()


def task_json(request: Request, task: Task) -> dict[str, Any]:
    container = get_container(request)
    now = container.clock.now()
    info = retention_info(task, now, container.retention.retention)
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value,
        "status_label": STATUS_LABELS[task.status],
        "source": task.source.value,
        "requester_dept": task.requester_dept,
        "requested_at": _iso(task.requested_at),
        "due_at": _iso(task.due_at),
        "due_badge": due_badge(task, now),
        "module_tag": task.module_tag,
        "closed_at": _iso(task.closed_at),
        "gk_link": safe_link(task.gk_link or "", [str(container.settings.gknowledge_base_url)]),
        "retention_days_left": info.days_left if info else None,
        "retention_alert": bool(info and info.alert),
    }


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@router.get("")
def list_tasks(
    request: Request, status: Annotated[list[StatusName] | None, Query()] = None
) -> JSONResponse:
    statuses = [TaskStatus(s) for s in status] if status else None
    tasks = get_container(request).tasks.list_tasks(statuses)
    return JSONResponse([task_json(request, t) for t in tasks])


@router.get("/{task_id}")
def get_task(request: Request, task_id: int) -> JSONResponse:
    container = get_container(request)
    try:
        task = container.tasks.get(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    body = task_json(request, task)
    body["evidence"] = container.tasks.evidence(task_id)
    body["mail_refs"] = [
        {"id": ref.id, "received_at": _iso(ref.received_at), "summary": ref.summary}
        for ref in container.mail.refs_for_task(task_id)
    ]
    return JSONResponse(body)


def _apply(request: Request, action: Any) -> JSONResponse:
    try:
        task = action()
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    except TransitionError as exc:
        return _error(str(exc), 409)
    except UserInputError as exc:
        return _error(str(exc), 400)
    return JSONResponse(task_json(request, task))


@router.post("")
def add_manual(request: Request, body: ManualBody) -> JSONResponse:
    tasks = get_container(request).tasks
    return _apply(request, lambda: tasks.add_manual(body.title, body.to_edit()))


@router.post("/{task_id}/approve")
def approve(request: Request, task_id: int, body: EditBody | None = None) -> JSONResponse:
    tasks = get_container(request).tasks
    edit = body.to_edit() if body else None
    return _apply(request, lambda: tasks.approve(task_id, edit))


@router.post("/{task_id}/dismiss")
def dismiss(request: Request, task_id: int) -> JSONResponse:
    tasks = get_container(request).tasks
    return _apply(request, lambda: tasks.dismiss(task_id))


@router.post("/{task_id}/edit")
def edit(request: Request, task_id: int, body: EditBody) -> JSONResponse:
    tasks = get_container(request).tasks
    return _apply(request, lambda: tasks.edit(task_id, body.to_edit()))


@router.post("/{task_id}/status")
def change_status(request: Request, task_id: int, body: StatusBody) -> JSONResponse:
    tasks = get_container(request).tasks
    return _apply(request, lambda: tasks.change_status(task_id, TaskStatus(body.status)))
