"""로컬 데이터 관리 API(사용자 전체 삭제, 기획서 5장 동의·삭제)."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.deps import get_container

router = APIRouter(prefix="/api/data")
logger = logging.getLogger(__name__)


class WipeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


@router.post("/wipe")
def wipe(request: Request, body: WipeRequest) -> JSONResponse:
    container = get_container(request)
    deleted = container.db.wipe_all()
    container.tasks.clear_cache()
    logger.info("로컬 데이터 전체 삭제: %d행", deleted)
    return JSONResponse({"deleted": deleted})
