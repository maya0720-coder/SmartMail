"""제안된 업무 트리아지 API(TODO-01·02, 결정기록 0015).

- `GET /api/tasks/suggested/summary`: 보이는 제안 수·'나중에' 수
- `GET /api/tasks/{id}/context`: 근거 메일 앞부분·요청 문장 위치(원본을 다시 읽음, 저장·로그 없음)
- `POST /api/tasks/{id}/snooze`: 다음 근무일 아침까지 숨김 + 되돌리기 토큰
- `POST /api/tasks/{id}/dismiss`: 업무 아님 + 학습(보낸 사람·말머리 키 해시만 기록)
- `POST /api/tasks/{id}/revert`: 60초 안 결정 취소(토큰 일회용, 업무 아님 취소 때 학습 1 감소)
- `GET·DELETE /api/triage-rules`: 학습 규칙 수 조회·전체 초기화(키 파일도 폐기)
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.container import Container
from app.deps import get_container
from app.routes.tasks import _apply, _decide, _error, _iso, task_json
from domain.clock import KST
from domain.mail import CollectorUnavailableError
from domain.models import TaskStatus
from domain.tasks.context import context_lines
from domain.tasks.service import TaskNotFoundError, TransitionError
from domain.tasks.triage_rules import RuleKey

router = APIRouter(prefix="/api/tasks")
rules_router = APIRouter(prefix="/api/triage-rules")
logger = logging.getLogger(__name__)

REVERT_EXPIRED = "되돌릴 수 있는 시간이 지났습니다"
MAX_LEARN_REFS = 5  # 학습할 받은 메일을 찾을 때 읽는 최대 메일 수


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


class RevertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_token: str = Field(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.get("/suggested/summary")
def suggested_summary(request: Request) -> JSONResponse:
    container = get_container(request)
    suggested = container.tasks.list_tasks([TaskStatus.SUGGESTED])
    snoozed = [t for t in suggested if container.tasks.is_snoozed(t)]
    wake = min((t.snooze_until for t in snoozed if t.snooze_until), default=None)
    today = container.clock.now().astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return JSONResponse(
        {
            "visible": len(suggested) - len(snoozed),
            "snoozed": len(snoozed),
            "next_wake": _iso(wake),
            # 오늘 정리 진행 막대: 오늘 내린 결정 수(되돌린 것은 뺌)
            "decided_today": container.tasks.triage_decisions_since(today),
        }
    )


@router.get("/{task_id}/context")
def context(request: Request, task_id: int) -> JSONResponse:
    """근거 메일 보기. 원본은 메모리에서만 쓰고 저장·로그하지 않는다(MAIL-08)."""
    container = get_container(request)
    try:
        container.tasks.get(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    refs = sorted(container.mail.refs_for_task(task_id), key=lambda r: r.received_at)
    empty: dict[str, object] = {
        "missing": True,
        "subject": None,
        "lines": [],
        "highlight": None,
        "attachments": [],
    }
    if not refs or refs[0].id is None:
        return JSONResponse({**empty, "thread_count": len(refs)})
    try:
        view = container.mail.view(refs[0].id)
    except CollectorUnavailableError as exc:
        return _error(str(exc), 503)
    if view is None or view.message is None:
        return JSONResponse({**empty, "thread_count": len(refs)})
    message = view.message
    lines, highlight = context_lines(message.body)
    return JSONResponse(
        {
            "missing": False,
            "subject": message.subject[:300],
            "sender_name": message.sender_name,
            "received_at": message.received_at.astimezone(KST).isoformat(),
            "lines": lines,
            "highlight": None
            if highlight is None
            else {"line": highlight.line, "start": highlight.start, "end": highlight.end},
            "attachments": list(message.attachment_names),  # 파일명만(MAIL-06)
            "thread_count": len(refs),
        }
    )


@router.post("/{task_id}/snooze")
def snooze(request: Request, task_id: int) -> JSONResponse:
    container = get_container(request)
    try:
        before = container.tasks.get(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    response = _apply(request, lambda: container.tasks.snooze(task_id))
    if response.status_code != 200:
        return response
    task = container.tasks.get(task_id)
    return JSONResponse(
        {
            **task_json(request, task),
            "decision_token": container.decisions.issue("snoozed", before),
        }
    )


def _learn_dismissal(container: Container, task_id: int) -> tuple[RuleKey, ...]:
    """업무 아님 학습. 원본 메일은 메모리에서만 읽고 키 해시만 남긴다(원문 저장·로그 없음).

    감점 기준과 같게 '받은' 첫 메일만 쓴다(내가 보낸 메일의 내 주소는 수집하지 않음).
    """
    refs = sorted(container.mail.refs_for_task(task_id), key=lambda r: r.received_at)
    for ref in refs[:MAX_LEARN_REFS]:
        if ref.id is None:
            continue
        try:
            view = container.mail.view(ref.id)
        except CollectorUnavailableError:
            logger.info("업무 아님 학습 생략: 메일을 읽을 수 없음")
            return ()
        if view is None or view.message is None or view.message.sent_by_me:
            continue
        return container.tasks.learn_dismissal(view.message.sender_address, view.message.subject)
    return ()


@router.post("/{task_id}/dismiss")
def dismiss(request: Request, task_id: int) -> JSONResponse:
    container = get_container(request)
    return _decide(
        request,
        task_id,
        "dismissed",
        lambda: container.tasks.dismiss(task_id),
        learn=lambda: _learn_dismissal(container, task_id),
    )


@router.post("/{task_id}/revert")
def revert(request: Request, task_id: int, body: RevertBody) -> JSONResponse:
    return revert_one(request, task_id, body.decision_token)


def revert_one(request: Request, task_id: int, token: str) -> JSONResponse:
    """건별 되돌리기(일괄 되돌리기도 건마다 이것을 쓴다, 결정기록 0036)."""
    container = get_container(request)
    decision = container.decisions.take(task_id, token)
    if decision is None:
        return _error(REVERT_EXPIRED, 409)
    try:
        task = container.tasks.revert_triage(decision.decision, decision.before)
    except TransitionError as exc:
        return _error(str(exc), 409)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    if decision.decision == "dismissed":
        container.tasks.unlearn(decision.learned)
    return JSONResponse({**task_json(request, task), "reverted": decision.decision})


@rules_router.get("")
def rule_count(request: Request) -> JSONResponse:
    return JSONResponse({"rules": get_container(request).tasks.learned_rule_count()})


@rules_router.delete("")
def reset_rules(request: Request, body: ConfirmBody) -> JSONResponse:
    """학습 초기화: 규칙 전체 삭제 + 로컬 키 폐기(예전 해시는 다시 계산할 수 없음)."""
    deleted = get_container(request).tasks.reset_learning()
    logger.info("업무 아님 학습 초기화: %d개", deleted)
    return JSONResponse({"deleted": deleted})
