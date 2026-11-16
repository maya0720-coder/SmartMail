"""업무(To-do) API(TODO-02~07). 입력은 허용 목록·길이로 검증하고 대량 할당을 막는다."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from app.routes.task_fields import first_ref, last_mail, requester_fields, thread_fields
from domain.clock import KST
from domain.errors import UserInputError
from domain.models import STATUS_LABELS, Task, TaskStatus, summary_state_of
from domain.retention_rules import retention_info
from domain.tasks.progress import progress_stale, ref_title, rule_summary
from domain.tasks.service import TaskEdit, TaskNotFoundError, TransitionError, due_badge
from domain.tasks.triage_view import is_low_confidence, received_group
from security.links import safe_link

router = APIRouter(prefix="/api/tasks")

# 구분(사용자 정의 목록). 목록에 있는지는 서비스가 검사한다(결정기록 0014).
ModuleTag = Annotated[str, Field(min_length=1, max_length=20)]
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
    weekly_section: Literal["done", "planned", "issues", "none"] | None = None

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
            weekly_section=self.weekly_section,
        )


class ManualBody(EditBody):
    title: str = Field(min_length=1, max_length=200)


class StatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: StatusName


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(KST).isoformat()


def task_json(request: Request, task: Task, running: set[int] | None = None) -> dict[str, Any]:
    container = get_container(request)
    now = container.clock.now()
    info = retention_info(task, now, container.retention.retention)
    refs = container.mail.refs_for_task(task.id) if task.id else []
    if running is None:
        running = container.summary_job.running_ids()
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
        # 제안 트리아지(결정기록 0015): 이유는 코드값, 문장은 담지 않는다
        "confidence": task.confidence,
        "reasons": list(task.reasons),
        "weekly_section": task.weekly_section,
        "snoozed": container.tasks.is_snoozed(task),
        "snooze_until": _iso(task.snooze_until),
        "thread_count": len(refs),
        # 요약 상태·스레드 최종 메일(결정기록 0028·0029). 기존 필드는 그대로 둔다
        **thread_fields(task, refs, running, _iso),
        # 요청자 구분·외부 회사명(결정기록 0027). requester_dept는 그대로 둔다
        **requester_fields(container.companies, task),
    }


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@router.get("")
def list_tasks(
    request: Request,
    status: Annotated[list[StatusName] | None, Query()] = None,
    snoozed: Literal["hide", "only"] = "hide",
) -> JSONResponse:
    statuses = [TaskStatus(s) for s in status] if status else None
    container = get_container(request)
    tasks = container.tasks.list_tasks(statuses)
    suggested_view = statuses == [TaskStatus.SUGGESTED]
    if suggested_view:
        # 제안 목록: '나중에'로 미룬 업무는 다시 보일 시각 전까지 뺀다(결정기록 0015).
        # snoozed=only면 미룬 업무만 돌려준다('나중에 n건' 펼침).
        want = snoozed == "only"
        tasks = [t for t in tasks if container.tasks.is_snoozed(t) == want]
    # 아직 AI 요청 요약을 만들지 않은 업무(제목이 메일 제목에서 온 임시 제목)
    pending = container.summary_job.pending_ids()
    running = container.summary_job.running_ids()
    now = container.clock.now()
    return JSONResponse(
        [
            {
                **task_json(request, t, running),
                "summary_pending": t.id in pending,
                # 받은 시각 묶음(오늘·어제·이번 주·이전): 스레드 마지막 메일 기준(결정기록 0028)
                "group": received_group(_group_time(container, t), now),
                "low_confidence": is_low_confidence(t.confidence),
            }
            for t in tasks
        ]
    )


def _group_time(container: Any, task: Task) -> datetime:
    refs = container.mail.refs_for_task(task.id) if task.id else []
    at, _ = last_mail(task, refs)
    return at or task.created_at


@router.get("/{task_id}")
def get_task(request: Request, task_id: int) -> JSONResponse:
    container = get_container(request)
    try:
        task = container.tasks.get(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    body = task_json(request, task)
    body["evidence"] = container.tasks.evidence(task_id)
    refs = container.mail.refs_for_task(task_id)
    body["mail_refs"] = [
        {
            "id": ref.id,
            "received_at": _iso(ref.received_at),
            "summary": ref.summary,
            # 방향·요약 상태(결정기록 0028·0029). title은 AI 요약이 끝난 메일만
            "direction": ref.direction,
            "summary_state": summary_state_of(ref),
            "title": ref_title(ref),
        }
        for ref in refs
    ]
    # 구조화 요약(요청 사항·기한·필요 조치, 마스킹된 AI 요약, 결정기록 0021). 첫 메일 기준
    first = first_ref(refs)
    body["digest"] = first.digest if first is not None else None
    # 진행 경과(결정기록 0028): 규칙 요약 + 저장된 AI 진행 요약(마스킹)과 새 메일 여부
    body["progress"] = rule_summary(refs)
    body["progress_digest"] = task.progress_digest
    body["progress_stale"] = progress_stale(task, refs)
    return JSONResponse(body)


def _decide(
    request: Request, task_id: int, decision: str, action: Any, learn: Any = None
) -> JSONResponse:
    """제안 결정(할 일로·업무 아님). 결정 직전 값을 보관하고 되돌리기 토큰을 함께 돌려준다.

    learn: 제안을 '업무 아님'으로 고른 직후 학습 키를 기록하고 그 키를 돌려주는 함수(선택).
    """
    container = get_container(request)
    try:
        before = container.tasks.get(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    response = _apply(request, action)
    if response.status_code != 200 or before.status is not TaskStatus.SUGGESTED:
        return response
    learned = learn() if learn is not None else ()
    body = json.loads(bytes(response.body))
    body["decision_token"] = container.decisions.issue(decision, before, learned)
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
    return _decide(request, task_id, "approved", lambda: tasks.approve(task_id, edit))


# '업무 아님'(POST /{task_id}/dismiss)은 학습 기록 때문에 routes/triage.py에 있다.


@router.post("/{task_id}/edit")
def edit(request: Request, task_id: int, body: EditBody) -> JSONResponse:
    tasks = get_container(request).tasks
    return _apply(request, lambda: tasks.edit(task_id, body.to_edit()))


@router.post("/{task_id}/status")
def change_status(request: Request, task_id: int, body: StatusBody) -> JSONResponse:
    tasks = get_container(request).tasks
    return _apply(request, lambda: tasks.change_status(task_id, TaskStatus(body.status)))
