"""로그인·로그아웃 API(AUTH-01~05, AUTH-08, AUTH-09).

- ID·암호는 JSON 본문으로만 받는다. 쿼리 문자열이 붙은 요청은 거부한다.
- 응답·로그에 입력값을 넣지 않는다. 실패 시 일반 메시지만 돌려준다.
- 세션 핸들 ID는 브라우저로 보내지 않는다(서버 메모리에만 있음).
"""

from __future__ import annotations

import threading
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from security.credentials import (
    MAX_SECRET_BYTES,
    MAX_USER_ID_BYTES,
    InvalidLoginInputError,
    LoginFailedError,
    LoginUnavailableError,
)
from security.logging_policy import Site

router = APIRouter(prefix="/api")

SiteName = Literal["wiki", "g-knowledge", "ai-ground", "groupware"]

LOGIN_FAILED_MESSAGE = "로그인하지 못했습니다. 입력 내용을 확인해 주세요."
LOGIN_UNAVAILABLE_MESSAGE = (
    "이 사이트의 로그인 연동이 아직 준비되지 않았습니다(연동 규격 확인 필요)."
)
QUERY_REJECTED_MESSAGE = "로그인 정보는 주소에 넣을 수 없습니다."
RATE_LIMITED_MESSAGE = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요."
LOGIN_IN_PROGRESS_MESSAGE = "이미 로그인 화면이 열려 있습니다. 열린 창에서 로그인을 마쳐 주세요."

_BROWSER_LOGIN_LOCKS: dict[Site, threading.Lock] = {}


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    site: SiteName
    # 문자 수 제한(바이트 제한은 credentials 모듈에서 다시 검사)
    user_id: str = Field(min_length=1, max_length=MAX_USER_ID_BYTES)
    password: str = Field(min_length=1, max_length=MAX_SECRET_BYTES)


class SiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: SiteName


def _error(message: str, status: int, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers=headers)


@router.post("/login")
def login(request: Request, body: LoginRequest) -> JSONResponse:
    if request.url.query:
        return _error(QUERY_REJECTED_MESSAGE, 400)
    container = get_container(request)
    site = Site(body.site)
    retry_after = container.login_limiter.check(site.value)
    if retry_after is not None:
        return _error(RATE_LIMITED_MESSAGE, 429, {"Retry-After": str(retry_after)})
    # 불변 문자열에서 덮어쓸 수 있는 버퍼로 옮기고, 모델 참조를 바로 끊는다.
    user_id = bytearray(body.user_id, "utf-8")
    secret = bytearray(body.password, "utf-8")
    del body
    try:
        container.login.login(site, user_id, secret)
    except InvalidLoginInputError:
        return _error(LOGIN_FAILED_MESSAGE, 400)
    except LoginUnavailableError:
        return _error(LOGIN_UNAVAILABLE_MESSAGE, 503)
    except LoginFailedError:
        return _error(LOGIN_FAILED_MESSAGE, 401)
    return JSONResponse({"site": site.value, "logged_in": True})


@router.post("/login/browser")
def login_browser(request: Request, body: SiteRequest) -> JSONResponse:
    """사이트 로그인 화면을 띄워 사용자가 직접 로그인한다(AUTH-09)."""
    container = get_container(request)
    site = Site(body.site)
    retry_after = container.login_limiter.check(site.value)
    if retry_after is not None:
        return _error(RATE_LIMITED_MESSAGE, 429, {"Retry-After": str(retry_after)})
    # 사이트별로 진행 중인 브라우저 로그인은 1건만 허용한다(브라우저 스레드 점유 방지).
    lock = _BROWSER_LOGIN_LOCKS.setdefault(site, threading.Lock())
    if not lock.acquire(blocking=False):
        return _error(LOGIN_IN_PROGRESS_MESSAGE, 409)
    try:
        container.login.login_interactive(site)
    except LoginUnavailableError:
        return _error(LOGIN_UNAVAILABLE_MESSAGE, 503)
    except LoginFailedError:
        return _error(LOGIN_FAILED_MESSAGE, 401)
    finally:
        lock.release()
    return JSONResponse({"site": site.value, "logged_in": True})


@router.post("/logout")
def logout(request: Request, body: SiteRequest) -> JSONResponse:
    get_container(request).sessions.logout(Site(body.site))
    return JSONResponse({"site": body.site, "logged_in": False})


@router.post("/logout-all")
def logout_all(request: Request) -> JSONResponse:
    count = get_container(request).sessions.logout_all()
    return JSONResponse({"logged_out": count})


@router.get("/connections")
def connections(request: Request) -> JSONResponse:
    container = get_container(request)
    settings = container.settings
    # 상태 칩을 누르면 여는 사이트 주소(설정에서 검증한 https 주소, 자격증명·쿼리 없음)
    site_urls = {
        Site.WIKI: str(settings.wiki_base_url),
        Site.GKNOWLEDGE: str(settings.gknowledge_base_url),
        Site.AIGROUND: str(settings.aiground_base_url),
        Site.GROUPWARE: str(settings.groupware_mail_base_url),
    }
    return JSONResponse(
        [
            {
                "site": status.site.value,
                "logged_in": status.logged_in,
                "browser_login": container.login.uses_browser_login(status.site),
                # 이번 실행에서 연동 기능에 로그인이 필요한지(가짜 어댑터면 false, 결정기록 0034)
                "needs_login": status.site not in container.login_free_sites,
                "url": site_urls.get(status.site, ""),
                # 로그인 안 됨일 때 마지막 종료 사유(결정기록 0031). 코드 값만 담는다
                "ended_reason": None if status.ended_reason is None else status.ended_reason.value,
                "keep_while_running": container.sessions.keep_while_running,
            }
            for status in container.sessions.status()
        ]
    )
