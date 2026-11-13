"""한 화면 일괄 로그인 API(AUTH-01·03·05·09, 결정기록 0011).

- 이미 로그인된 사이트는 건너뛰고, 입력도 요구하지 않는다.
- 회사 계정 사이트는 다른 회사 계정 사이트가 로그인돼 있으면 SSO로 입력 없이 로그인한다.
- ID·암호는 JSON 본문으로만 받아 곧바로 bytearray로 옮기고, 끝나면 0으로 덮어쓴다.
  어디에도 저장하지 않는다.
- 응답·로그에는 사이트 이름과 성공 여부만 담는다.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.container import Container
from app.deps import get_container
from app.routes.auth import SiteName
from security.credentials import (
    MAX_SECRET_BYTES,
    MAX_USER_ID_BYTES,
    CredentialsRequiredError,
    InvalidLoginInputError,
    LoginFailedError,
    LoginUnavailableError,
    wipe,
)
from security.logging_policy import Site

router = APIRouter(prefix="/api/login")

# 일괄 로그인 대상(그룹웨어는 연동 규격 확인 전이라 제외)
COMPANY_SITES: Final = (Site.AIGROUND, Site.GKNOWLEDGE)
COMPANY_LIMIT_KEY: Final = "company-account"  # 회사 계정 시도 횟수 공용 키
ALL_SITES: Final = (*COMPANY_SITES, Site.WIKI)

FAILED: Final = "로그인하지 못했습니다. 입력 내용을 확인해 주세요."
NEEDS_INPUT: Final = "로그인 정보가 필요합니다."
RATE_LIMITED: Final = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요."
UNAVAILABLE: Final = "이 사이트의 자동 로그인을 쓸 수 없습니다."


class LoginAllRequest(BaseModel):
    """시작 로그인 창 입력. ID 1개 + 공통 암호 1개(위키 암호가 다르면 wiki_password)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    user_id: str = Field(default="", max_length=MAX_USER_ID_BYTES)
    password: str = Field(default="", max_length=MAX_SECRET_BYTES)
    wiki_password: str = Field(default="", max_length=MAX_SECRET_BYTES)  # 비우면 공통 암호
    only: SiteName | None = None  # 이 사이트만 로그인(다른 화면에서 필요할 때)


def account_ids(user_id: str, domain: str) -> tuple[str, str]:
    """입력 ID → (회사 계정 ID@도메인, 위키·그룹웨어 ID). '@'까지 입력했으면 그대로 쓴다."""
    plain = user_id.strip()
    if any(ch.isspace() or ord(ch) < 0x20 for ch in plain):
        raise ValueError("ID에 공백이나 제어 문자를 쓸 수 없습니다")
    if not plain:
        return "", ""
    local = plain.split("@", 1)[0]
    return (plain if "@" in plain else f"{plain}@{domain}"), local


def pending_sites(container: Container) -> list[Site]:
    """자동 로그인 대상 중 아직 로그인되지 않은 사이트."""
    return [
        site
        for site in ALL_SITES
        if container.login.supports_password(site) and container.sessions.current(site) is None
    ]


def _login_one(
    container: Container, site: Site, user_id: bytearray, secret: bytearray
) -> str | None:
    """사이트 1개 로그인. 성공이면 None, 실패면 화면 안내 문구."""
    company = site in COMPANY_SITES
    if not company and (not user_id or not secret):
        return NEEDS_INPUT  # 시도 횟수를 쓰지 않는다(보안 검토 L5)
    # 회사 계정 사이트는 같은 계정이므로 시도 횟수를 함께 센다(보안 검토 M4)
    if container.login_limiter.check(COMPANY_LIMIT_KEY if company else site.value) is not None:
        return RATE_LIMITED
    if not user_id or not secret:
        # 입력이 없으면 SSO만 시도한다(다른 회사 계정 사이트가 로그인돼 있을 때 성공).
        if container.login.try_sso(site) is not None:
            return None
        return NEEDS_INPUT
    try:
        # 서비스가 받은 버퍼를 0으로 덮어쓰므로 사본을 넘긴다(원본은 호출자가 지운다).
        container.login.login(site, bytearray(user_id), bytearray(secret))
    except CredentialsRequiredError:
        return NEEDS_INPUT
    except (InvalidLoginInputError, LoginFailedError):
        return FAILED
    except LoginUnavailableError:
        return UNAVAILABLE
    return None


@router.get("/pending")
def pending(request: Request) -> JSONResponse:
    """입력이 필요한 계정 묶음.

    회사 계정 사이트가 하나라도 로그인돼 있으면 회사 계정은 묻지 않는다(SSO로 연결).
    """
    container = get_container(request)
    sites = pending_sites(container)
    company_live = any(container.sessions.current(s) is not None for s in COMPANY_SITES)
    return JSONResponse(
        {
            "sites": [s.value for s in sites],
            "need_company": any(s in COMPANY_SITES for s in sites) and not company_live,
            "need_wiki": Site.WIKI in sites,
            "account_domain": container.account_domain,
            # '나중에'를 누르면 이번 실행 동안 시작 로그인 창을 자동으로 띄우지 않는다
            "dismissed": container.login_prompt_dismissed,
        }
    )


@router.post("/later")
def later(request: Request) -> JSONResponse:
    get_container(request).login_prompt_dismissed = True
    return JSONResponse({"dismissed": True})


@router.post("/all")
def login_all(request: Request, body: LoginAllRequest) -> JSONResponse:
    if request.url.query:
        return JSONResponse({"error": "로그인 정보는 주소에 넣을 수 없습니다."}, status_code=400)
    container = get_container(request)
    only = None if body.only is None else Site(body.only)
    buffers: list[bytearray] = []
    results = []
    try:
        try:
            # 변환 실패(잘못된 유니코드 등)도 이미 만든 버퍼를 지우도록 try 안에서 만든다(L2)
            company_id, wiki_id = account_ids(body.user_id, container.account_domain)
            for value in (company_id, body.password, wiki_id):
                buffers.append(bytearray(value, "utf-8"))
            buffers.append(bytearray(body.wiki_password or body.password, "utf-8"))
        except (UnicodeError, ValueError):
            return JSONResponse({"error": FAILED}, status_code=400)
        finally:
            del body  # 불변 문자열 참조를 바로 끊는다(결정기록 0005)
        company, wiki = (buffers[0], buffers[1]), (buffers[2], buffers[3])
        company_failed = False
        for site in pending_sites(container):
            if only is not None and site is not only:
                continue
            is_company = site in COMPANY_SITES
            if is_company and company_failed:
                error: str | None = FAILED  # 같은 계정으로 다시 시도하지 않음(M4)
            else:
                user_id, secret = company if is_company else wiki
                error = _login_one(container, site, user_id, secret)
                company_failed = company_failed or (is_company and error == FAILED)
            results.append({"site": site.value, "logged_in": error is None, "error": error})
    finally:
        for buffer in buffers:
            wipe(buffer)
        container.login_progress.update("")
    # AI-Ground에 로그인됐으면 밀린 업무 제목 요약을 바로 시작한다(결정기록 0014)
    if any(r["logged_in"] and r["site"] == Site.AIGROUND.value for r in results):
        container.summary_job.kick()
    return JSONResponse({"results": results})


@router.get("/progress")
def progress(request: Request) -> JSONResponse:
    """2단계 인증 안내(승인 대기 여부·번호). 자격증명은 담지 않는다."""
    return JSONResponse(get_container(request).login_progress.snapshot())
