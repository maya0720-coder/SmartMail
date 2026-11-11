"""히스토리 카드·3단계 이력 조회·보관 알림 API(HIST-01~06, SRCH-01~05, RET-02)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from app.routes.tasks import ModuleTag, task_json
from domain.errors import UserInputError
from domain.history_service import CardDraft, HistoryHit
from domain.models import TaskStatus, Visibility
from domain.retention_rules import tasks_needing_alert
from domain.search.pipeline import SearchOutcome
from domain.tasks.service import TaskNotFoundError

router = APIRouter(prefix="/api")

BADGES = {"app": "앱", "gk": "G-Knowledge", "ai": "AI"}


class CardBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem: str = Field(min_length=1, max_length=2000)
    cause: str = Field(default="", max_length=2000)
    action: str = Field(min_length=1, max_length=2000)
    tcode: str | None = Field(default=None, max_length=100)
    tags: list[Annotated[str, Field(max_length=30)]] = Field(default_factory=list, max_length=10)
    visibility: Literal["private", "team", "company"] = "team"


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    task_id: int | None = Field(default=None, ge=1)
    start_from: Literal["app", "gk", "ai"] = "app"
    module: ModuleTag | None = None


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: int | None = Field(default=None, ge=1)
    helpful: bool


def _task_exists(request: Request, task_id: int) -> bool:
    try:
        get_container(request).tasks.get(task_id)
    except TaskNotFoundError:
        return False
    return True


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _hit_json(hit: HistoryHit) -> dict[str, Any]:
    card = hit.card
    return {
        "task_id": card.task_id,
        "title": hit.task.title if hit.task else card.problem,
        "problem": card.problem,
        "cause": card.cause,
        "action": card.action,
        "tcode": card.tcode,
        "tags": list(card.tags),
        "visibility": card.visibility.value,
        "module_tag": hit.task.module_tag if hit.task else None,
        "gk_link": hit.task.gk_link
        if hit.task
        else None,  # 표시 여부만 사용(JS는 링크로 그리지 않음)
    }


def outcome_json(outcome: SearchOutcome) -> dict[str, Any]:
    stages = []
    for stage in outcome.stages:
        item: dict[str, Any] = {
            "stage": stage.stage,
            "badge": BADGES[stage.stage],
            "ran": stage.ran,
            "found": stage.found,
            "notice": stage.notice,
            "app_hits": [_hit_json(h) for h in stage.app_hits],
            "gk_hits": [
                {"title": h.title, "link": h.link, "snippet": h.snippet} for h in stage.gk_hits
            ],
            "ai_answer": None,
        }
        if stage.ai_answer is not None:
            item["ai_answer"] = {
                "text": stage.ai_answer.text,
                "references": list(stage.ai_answer.references),
                "label": stage.ai_answer.label,
            }
        stages.append(item)
    return {"resolved": outcome.resolved.value, "message": outcome.message, "stages": stages}


@router.get("/history")
def list_history(
    request: Request,
    q: Annotated[str, Query(max_length=200)] = "",
    module: ModuleTag | None = None,
    tag: Annotated[str | None, Query(max_length=30)] = None,
    days: Annotated[int | None, Query(ge=1, le=3650)] = None,
) -> JSONResponse:
    hits = get_container(request).history.search(q, module=module, tag=tag, within_days=days)
    return JSONResponse([_hit_json(h) for h in hits])


@router.post("/history/{task_id}/draft")
def draft_card(request: Request, task_id: int) -> JSONResponse:
    try:
        draft = get_container(request).history.draft(task_id)
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    return JSONResponse(
        {
            "problem": draft.problem,
            "cause": draft.cause,
            "action": draft.action,
            "tcode": draft.tcode,
        }
    )


@router.post("/history/{task_id}")
def save_card(request: Request, task_id: int, body: CardBody) -> JSONResponse:
    container = get_container(request)
    draft = CardDraft(body.problem, body.cause, body.action, body.tcode)
    try:
        card = container.history.save(task_id, draft, tuple(body.tags), Visibility(body.visibility))
    except TaskNotFoundError:
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    except UserInputError as exc:
        return _error(str(exc), 400)
    return JSONResponse(_hit_json(HistoryHit(card, container.tasks.get(task_id))))


@router.post("/search")
def search(request: Request, body: SearchBody) -> JSONResponse:
    container = get_container(request)
    if body.task_id is not None and not _task_exists(request, body.task_id):
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    try:
        outcome = container.search.search(
            body.query, task_id=body.task_id, start_from=body.start_from, module=body.module
        )
    except UserInputError as exc:
        return _error(str(exc), 400)
    return JSONResponse(outcome_json(outcome))


@router.post("/search/feedback")
def feedback(request: Request, body: FeedbackBody) -> JSONResponse:
    if body.task_id is not None and not _task_exists(request, body.task_id):
        return _error("요청한 항목을 찾을 수 없습니다.", 404)
    get_container(request).search.feedback(body.task_id, body.helpful)
    return JSONResponse({"recorded": True})


@router.get("/alerts")
def alerts(request: Request) -> JSONResponse:
    """삭제 2일 전 G-Knowledge 미등록 완료 업무(RET-02)."""
    container = get_container(request)
    done = container.tasks.list_tasks([TaskStatus.DONE])
    targets = tasks_needing_alert(done, container.clock.now(), container.retention.retention)
    return JSONResponse([task_json(request, t) for t in targets])
