"""데이터 모드(실제 / 가상) 선택 API(결정기록 0042).

- `GET /api/run-mode`: 지금 실행 중인 모드, 저장된 화면 선택값, 환경 변수로 정해졌는지
- `PUT /api/run-mode {mode}`: 화면 선택값 저장(다음 시작 때 적용). CSRF는 공통 미들웨어.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.deps import get_container
from config.run_mode import (
    DEFAULT_MODE,
    RunMode,
    forced_by_env,
    load_saved_mode,
    run_mode_path,
    save_mode,
)
from config.settings import ConfigError

router = APIRouter(prefix="/api/run-mode")
logger = logging.getLogger(__name__)


class RunModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RunMode


def _state(request: Request) -> dict[str, object]:
    settings = get_container(request).settings
    current: RunMode = "demo" if settings.demo_mode else "real"
    try:
        saved = load_saved_mode(run_mode_path(settings))
    except ConfigError:
        saved = None
    forced = forced_by_env()
    effective = current if forced else (saved or DEFAULT_MODE)
    return {
        "current": current,
        "saved": saved,
        "default": DEFAULT_MODE,  # 고른 적이 없을 때 시작 모드
        "forced": forced,  # 실행 스크립트 -Demo 등 환경 변수로 정한 실행(화면 값보다 우선)
        "restart_needed": effective != current,
    }


@router.get("")
def get_run_mode(request: Request) -> JSONResponse:
    return JSONResponse(_state(request))


@router.put("")
def put_run_mode(request: Request, body: RunModeBody) -> JSONResponse:
    path = run_mode_path(get_container(request).settings)
    try:
        save_mode(path, body.mode)
    except OSError:
        logger.warning("데이터 모드 저장 실패")
        return JSONResponse({"error": "데이터 모드를 저장하지 못했습니다"}, status_code=500)
    logger.info("데이터 모드 선택 저장: %s(다음 시작 때 적용)", body.mode)
    return JSONResponse(_state(request))
