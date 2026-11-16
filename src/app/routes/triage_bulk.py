"""제안 일괄 처리 API(U2, TODO-02, 결정기록 0036).

- `POST /api/triage/bulk` `{ids[1~100], decision}`: 건별 결정 로직(할 일로·나중에·업무 아님)을
  그대로 한 건씩 실행하고 성공·실패 건을 돌려준다. '업무 아님' 학습도 건별로 기록된다.
  성공한 건의 되돌리기 토큰 n개를 묶은 **일괄 되돌리기 토큰** 1개를 발급한다.
- `decision: "category"`는 구분만 일괄로 바꾼다(결정이 아니므로 되돌리기 토큰 없음).
- `POST /api/triage/bulk/revert` `{bulk_token}`: 묶인 건을 모두 되돌린다(일회용, 60초).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.deps import get_container
from app.routes.tasks import ModuleTag, _apply, _error, approve
from app.routes.triage import REVERT_EXPIRED, dismiss, revert_one, snooze
from domain.models import TaskStatus
from domain.tasks.service import TaskEdit, TaskNotFoundError

router = APIRouter(prefix="/api/triage")

MAX_BULK: Final = 100
TaskId = Annotated[int, Field(gt=0, lt=2**31)]
BulkDecisionName = Literal["approve", "snooze", "dismiss", "category"]
NOT_SUGGESTED = "제안 상태인 항목만 일괄로 처리할 수 있습니다"


class BulkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[TaskId] = Field(min_length=1, max_length=MAX_BULK)
    decision: BulkDecisionName
    module_tag: ModuleTag | None = None

    @model_validator(mode="after")
    def _category_needs_tag(self) -> BulkBody:
        if (self.decision == "category") != (self.module_tag is not None):
            raise ValueError("구분 변경에만 module_tag를 함께 보냅니다")
        return self


class BulkRevertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bulk_token: str = Field(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def _error_text(response: JSONResponse) -> str:
    body = json.loads(bytes(response.body))
    return str(body.get("error") or "처리하지 못했습니다")


def _one(request: Request, decision: str, task_id: int, tag: str | None) -> JSONResponse:
    """한 건 처리. 건별 API와 같은 함수를 그대로 부른다(결과가 건별 결정과 같다)."""
    container = get_container(request)
    try:
        task = container.tasks.get(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    if task.status is not TaskStatus.SUGGESTED:
        return _error(NOT_SUGGESTED, 409)
    if decision == "approve":
        return approve(request, task_id, None)
    if decision == "snooze":
        return snooze(request, task_id)
    if decision == "dismiss":
        return dismiss(request, task_id)
    return _apply(request, lambda: container.tasks.edit(task_id, TaskEdit(module_tag=tag)))


@router.post("/bulk")
def bulk(request: Request, body: BulkBody) -> JSONResponse:
    container = get_container(request)
    ids = list(dict.fromkeys(body.ids))  # 중복 제거(순서 유지)
    succeeded: list[int] = []
    failed: list[dict[str, Any]] = []
    tokens: list[tuple[int, str]] = []
    for task_id in ids:
        response = _one(request, body.decision, task_id, body.module_tag)
        if response.status_code != 200:
            failed.append({"id": task_id, "error": _error_text(response)})
            continue
        succeeded.append(task_id)
        token = json.loads(bytes(response.body)).get("decision_token")
        if token:
            tokens.append((task_id, str(token)))
    bulk_token = container.decisions.issue_bulk(tokens) if tokens else None
    return JSONResponse(
        {
            "decision": body.decision,
            "succeeded": succeeded,
            "failed": failed,
            "bulk_token": bulk_token,
        }
    )


@router.post("/bulk/revert")
def bulk_revert(request: Request, body: BulkRevertBody) -> JSONResponse:
    container = get_container(request)
    found = container.decisions.take_bulk(body.bulk_token)
    if found is None:
        return _error(REVERT_EXPIRED, 409)
    reverted: list[int] = []
    failed: list[dict[str, Any]] = []
    for task_id, token in found.items:
        response = revert_one(request, task_id, token)
        if response.status_code == 200:
            reverted.append(task_id)
        else:
            failed.append({"id": task_id, "error": _error_text(response)})
    return JSONResponse({"reverted": reverted, "failed": failed})
