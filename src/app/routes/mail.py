"""메일 수집·원본 조회 API(MAIL-04·07~10)."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.bootstrap import select_collector
from app.deps import get_container
from domain.clock import KST
from domain.mail import CollectorUnavailableError
from domain.mail_service import MISSING_ORIGINAL
from security.logging_policy import EventType, Site, log_event

router = APIRouter(prefix="/api/mail")


class CollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # week: 이번 주(목~수, 기본) / recent7: 최근 7일
    period: Literal["week", "recent7"] = "week"


def _unavailable(exc: Exception) -> JSONResponse:
    message = str(exc) if isinstance(exc, CollectorUnavailableError) else "메일을 읽지 못했습니다"
    return JSONResponse({"error": message}, status_code=503)


@router.post("/detect")
def detect(request: Request) -> JSONResponse:
    container = get_container(request)
    collector, result = select_collector(container.settings, container.sessions, container.clock)
    old = container.mail.collector
    container.mail.collector = collector
    close = getattr(old, "close", None)
    if old is not collector and callable(close):
        close()
    container.mail_detection = result
    return JSONResponse({"kind": result.kind.value, "message": result.message})


@router.post("/collect")
def collect(request: Request, body: CollectRequest) -> JSONResponse:
    container = get_container(request)
    site = Site.OUTLOOK if container.mail.collector.source == "outlook" else Site.GROUPWARE
    try:
        if body.period == "recent7":
            now = container.clock.now()
            result = container.mail.collect(now - timedelta(days=7), now + timedelta(minutes=1))
        else:
            result = container.mail.collect()
    except CollectorUnavailableError as exc:
        log_event(EventType.MAIL_COLLECT, site, success=False)
        return _unavailable(exc)
    log_event(EventType.MAIL_COLLECT, site, success=True)
    ingest = container.tasks.ingest(result.threads)
    return JSONResponse(
        {
            "suggested": len(ingest.suggested),
            "linked": ingest.linked,
            "reply_detected": ingest.reply_detected,
            "threads": len(result.threads),
            "new_messages": result.new_messages,
            "excluded_messages": result.excluded_messages,
            "since": result.since.astimezone(KST).isoformat(),
            "until": result.until.astimezone(KST).isoformat(),
        }
    )


@router.get("/{ref_id}")
def view(request: Request, ref_id: int) -> JSONResponse:
    container = get_container(request)
    try:
        mail_view = container.mail.view(ref_id)
    except CollectorUnavailableError as exc:
        return _unavailable(exc)
    if mail_view is None:
        return JSONResponse({"error": "요청한 항목을 찾을 수 없습니다."}, status_code=404)
    message = mail_view.message
    if message is None:
        return JSONResponse({"missing": True, "notice": MISSING_ORIGINAL})
    return JSONResponse(
        {
            "missing": False,
            "subject": message.subject,
            "sender_name": message.sender_name,
            "received_at": message.received_at.astimezone(KST).isoformat(),
            "body": message.body,
            "attachments": list(message.attachment_names),
            "can_open": container.mail.collector.can_open_in_client,
        }
    )


@router.post("/{ref_id}/open")
def open_in_client(request: Request, ref_id: int) -> JSONResponse:
    opened = get_container(request).mail.open_in_client(ref_id)
    if not opened:
        return JSONResponse({"error": "아웃룩에서 원본을 열 수 없습니다."}, status_code=409)
    return JSONResponse({"opened": True})
