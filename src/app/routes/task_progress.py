"""업무 진행 경과 API(결정기록 0028, TODO-01·MAIL-03·MAIL-08).

- 타임라인: 스레드의 모든 메일을 시간순으로(방향·시각·요약 상태·AI 요약 제목).
  보낸 사람 이름은 원본을 다시 읽어 응답에만 담는다(저장 금지). 원본이 없으면 '원문 없음'.
- AI 진행 요약: 메일별 마스킹된 요약만 AI-Ground에 보낸다(원문·이름·주소 없음).
  CSRF는 공통 미들웨어, 속도 제한은 업무별 1분 3회.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.deps import get_container
from app.ratelimit import RateLimiter
from domain.ai import AiUnavailableError
from domain.clock import KST
from domain.mail import CollectorUnavailableError
from domain.models import summary_state_of
from domain.tasks.progress import (
    MAX_PROGRESS_REFS,
    ordered,
    progress_items,
    progress_stale,
    ref_title,
)
from domain.tasks.service import TaskNotFoundError

router = APIRouter(prefix="/api/tasks")

NOT_FOUND: Final = "요청한 항목을 찾을 수 없습니다."
NO_MAIL: Final = "메일이 없는 업무는 진행 요약을 만들 수 없습니다."
RATE_LIMITED: Final = "진행 요약 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
PROGRESS_MAX_PER_WINDOW: Final = 3
PROGRESS_WINDOW: Final = timedelta(minutes=1)


def _limiter(request: Request) -> RateLimiter:
    container = get_container(request)
    limiter = getattr(request.app.state, "progress_limiter", None)
    if limiter is None:
        limiter = RateLimiter(container.clock, PROGRESS_MAX_PER_WINDOW, PROGRESS_WINDOW)
        request.app.state.progress_limiter = limiter
    return limiter


@router.get("/{task_id}/timeline")
def timeline(request: Request, task_id: int) -> JSONResponse:
    container = get_container(request)
    try:
        container.tasks.get(task_id)
    except TaskNotFoundError:
        return JSONResponse({"error": NOT_FOUND}, status_code=404)
    items = []
    for ref in ordered(container.mail.refs_for_task(task_id))[-MAX_PROGRESS_REFS:]:
        sender: str | None = None
        missing = True
        if ref.id is not None:
            try:
                view = container.mail.view(ref.id)
            except CollectorUnavailableError:
                view = None
            if view is not None and view.message is not None:
                sender, missing = view.message.sender_name[:100], False
        items.append(
            {
                "id": ref.id,
                "received_at": ref.received_at.astimezone(KST).isoformat(),
                "direction": ref.direction,
                "summary_state": summary_state_of(ref),
                "title": ref_title(ref),
                "sender_name": sender,  # 화면 표시만, 저장하지 않음
                "missing": missing,
            }
        )
    return JSONResponse({"items": items}, headers={"cache-control": "no-store"})


@router.post("/{task_id}/progress-summary")
def progress_summary(request: Request, task_id: int) -> JSONResponse:
    container = get_container(request)
    try:
        task = container.tasks.get(task_id)
    except TaskNotFoundError:
        return JSONResponse({"error": NOT_FOUND}, status_code=404)
    refs = container.mail.refs_for_task(task_id)
    if not refs:
        return JSONResponse({"error": NO_MAIL}, status_code=400)
    retry = _limiter(request).check(f"progress:{task_id}")
    if retry is not None:
        return JSONResponse(
            {"error": RATE_LIMITED}, status_code=429, headers={"retry-after": str(retry)}
        )
    try:
        digest = container.ai.summarize_progress(progress_items(refs), task_id=task_id)
    except AiUnavailableError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    task = container.tasks.set_progress_digest(task_id, digest, len(refs))
    return JSONResponse(
        {
            "progress_digest": task.progress_digest,
            "progress_stale": progress_stale(task, refs),
        }
    )
