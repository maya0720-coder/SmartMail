"""주간보고 지난주 이월·마감 알림 API(U5·U6, WIKI-01·03, 결정기록 0037).

- `POST /api/weekly/carryover`: 지난주 내 보고의 '차주 계획'을 이번 주 업무와 비교한 결과.
  위키 세션이 있으면 위키 내 영역, 없으면 게시 캐시를 쓴다. 결과는 저장하지 않는다.
- `GET  /api/weekly/reminder`: 마감 알림 배너 상태
- `POST /api/weekly/reminder/hide`: 오늘 숨기기(서버 메모리)
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.deps import get_container

router = APIRouter(prefix="/api/weekly")


@router.post("/carryover")
def carryover(request: Request) -> JSONResponse:
    container = get_container(request)
    source, items = container.carryover.last_week(container.clock.now())
    return JSONResponse({"source": source, "items": [i.as_json() for i in items]})


@router.get("/reminder")
def reminder(request: Request) -> JSONResponse:
    return JSONResponse(get_container(request).reminder.state())


@router.post("/reminder/hide")
def hide_reminder(request: Request) -> JSONResponse:
    container = get_container(request)
    container.reminder.hide_today()
    return JSONResponse(container.reminder.state())
