"""업무 제목 AI 요청 요약 상태·실행 API(결정기록 0014)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.deps import get_container

router = APIRouter(prefix="/api/summaries")


@router.get("/status")
def status(request: Request) -> JSONResponse:
    return JSONResponse(get_container(request).summary_job.status())


@router.post("/run")
def run(request: Request) -> JSONResponse:
    """기다리지 않고 백그라운드에서 한 묶음 실행한다(남은 것은 주기 작업이 이어서 처리)."""
    job = get_container(request).summary_job
    if not job.ready():
        return JSONResponse({"error": "AI-Ground에 먼저 로그인해 주세요."}, status_code=409)
    job.kick()
    return JSONResponse({"started": True})
