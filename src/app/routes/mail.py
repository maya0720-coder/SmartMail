"""메일 수집·원본 조회 API(MAIL-04·07~10)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator

from app.bootstrap import select_collector
from app.deps import get_container
from app.mail_sync import collect_and_ingest
from domain.clock import KST
from domain.errors import UserInputError
from domain.mail import CollectorUnavailableError
from domain.mail_service import MISSING_ORIGINAL, CollectionResult

router = APIRouter(prefix="/api/mail")


class CollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # sync: 자동 수집과 같은 증분 수집(기본, 처음이면 최근 N일)
    # range: 수동 기간 지정(start~end, 날짜 포함) / week: 이번 주(목~수) / recent7: 최근 7일
    period: Literal["sync", "range", "week", "recent7"] = "sync"
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _range_dates(self) -> CollectRequest:
        has_dates = self.start is not None or self.end is not None
        if self.period == "range" and (self.start is None or self.end is None):
            raise ValueError("기간 지정 수집은 시작일과 종료일이 필요합니다")
        if self.period != "range" and has_dates:
            raise ValueError("시작일·종료일은 기간 지정 수집에서만 씁니다")
        return self


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


@router.get("/sync-status")
def sync_status(request: Request) -> JSONResponse:
    container = get_container(request)
    last = container.mail.last_synced_at
    return JSONResponse(
        {
            "last_synced_at": None if last is None else last.astimezone(KST).isoformat(),
            "initial_days": container.mail.initial_sync_days,
            "interval_minutes": container.settings.mail_sync_minutes,
        }
    )


@router.post("/collect")
def collect(request: Request, body: CollectRequest) -> JSONResponse:
    container = get_container(request)
    mail = container.mail

    def run() -> CollectionResult:
        if body.period == "range" and body.start is not None and body.end is not None:
            return mail.collect_range(body.start, body.end)
        if body.period == "recent7":
            now = container.clock.now()
            return mail.collect(now - timedelta(days=7), now + timedelta(minutes=1))
        if body.period == "week":
            return mail.collect()
        return mail.sync()

    try:
        payload = collect_and_ingest(container, run)
    except CollectorUnavailableError as exc:
        return _unavailable(exc)
    except UserInputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(payload)


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
