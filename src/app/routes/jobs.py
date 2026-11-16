"""작업 센터 API(U1, 결정기록 0035): 백그라운드 작업 상태 목록(메모리, 저장 없음)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.deps import get_container
from app.jobs import collect_jobs

router = APIRouter(prefix="/api/jobs")


@router.get("")
def list_jobs(request: Request) -> JSONResponse:
    return JSONResponse(collect_jobs(get_container(request)), headers={"cache-control": "no-store"})
