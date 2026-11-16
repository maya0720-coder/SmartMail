"""화면 배치 설정 API(결정기록 0016): 패널 폭 조회·저장·초기화.

상태 변경(PUT·DELETE)은 공통 보안 미들웨어의 CSRF 검사를 거친다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from app.ui_prefs import (
    ALL_KEYS,
    MAX_PANEL_WIDTH,
    MIN_PANEL_WIDTH,
    load_ui_prefs,
    reset_ui_prefs,
    save_ui_prefs,
)

router = APIRouter(prefix="/api/ui-prefs")


class UiPrefsBody(BaseModel):
    """바꿀 값만 보낸다. 허용 키(폭 2개·넓게 보기 1개) 외에는 거부한다."""

    model_config = ConfigDict(extra="forbid", strict=True)

    detail_w: int | None = Field(default=None, ge=MIN_PANEL_WIDTH, le=MAX_PANEL_WIDTH)
    triage_list_w: int | None = Field(default=None, ge=MIN_PANEL_WIDTH, le=MAX_PANEL_WIDTH)
    triage_wide: bool | None = None  # 제안 상세 넓게 보기(결정기록 0033)
    # 대시보드 기간(결정기록 0039)
    dashboard_grain: Literal["all", "year", "half", "quarter", "month", "week"] | None = None


@router.get("")
def get_prefs(request: Request) -> JSONResponse:
    return JSONResponse(load_ui_prefs(get_container(request).app_settings))


@router.put("")
def put_prefs(request: Request, body: UiPrefsBody) -> JSONResponse:
    store = get_container(request).app_settings
    save_ui_prefs(store, body.model_dump(exclude_none=True))
    return JSONResponse(load_ui_prefs(store))


@router.delete("")
def delete_prefs(request: Request) -> JSONResponse:
    """화면 배치 초기화: 저장한 폭을 모두 지운다."""
    store = get_container(request).app_settings
    reset_ui_prefs(store)
    return JSONResponse(load_ui_prefs(store))


@router.delete("/{key}")
def delete_pref(request: Request, key: str) -> JSONResponse:
    """설정 하나만 기본값으로 되돌린다(손잡이 더블클릭·Enter, 넓게 보기 끄기)."""
    if key not in ALL_KEYS:
        return JSONResponse({"error": "알 수 없는 설정입니다."}, status_code=400)
    store = get_container(request).app_settings
    reset_ui_prefs(store, [key])
    return JSONResponse(load_ui_prefs(store))
