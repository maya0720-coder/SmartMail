"""한 화면 일괄 로그인 API(AUTH-01·03·05·09, 결정기록 0011).

- 이미 로그인된 사이트는 건너뛰고, 입력도 요구하지 않는다.
- 회사 계정 사이트는 다른 회사 계정 사이트가 로그인돼 있으면 SSO로 입력 없이 로그인한다.
- ID·암호는 JSON 본문으로만 받아 곧바로 bytearray로 옮기고, 끝나면 0으로 덮어쓴다.
  어디에도 저장하지 않는다.
- 응답·로그에는 사이트 이름과 성공 여부만 담는다.
"""

from __future__ import annotations

import hmac
import re
from functools import lru_cache
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
    OtpFailedError,
    wipe,
)
from security.groupware_captcha import CaptchaFailedError, CaptchaPolicy, normalize, parse_policy
from security.logging_policy import Site
from security.login_flows import load_login_fields

router = APIRouter(prefix="/api/login")

# 일괄 로그인 대상. 그룹웨어는 자체 로그인(아이디만, 공통 암호) — 결정기록 0017
COMPANY_SITES: Final = (Site.AIGROUND, Site.GKNOWLEDGE)
COMPANY_LIMIT_KEY: Final = "company-account"  # 회사 계정 시도 횟수 공용 키
COMPANY_SSO_LIMIT_KEY: Final = "company-sso"  # 입력 없는 SSO 시도(암호 미제출) 횟수 키
# 시작 로그인 창이 한 번에 로그인하는 사이트. 그룹웨어는 빼고, 필요할 때 사용자가 직접
# (설정·연결의 '로그인' 버튼 → only=groupware) 로그인한다(사용자 결정 2026-11-15, 결정기록 0026)
AUTO_SITES: Final = (*COMPANY_SITES, Site.WIKI)
MANUAL_SITES: Final = (Site.GROUPWARE,)
ALL_SITES: Final = (*AUTO_SITES, *MANUAL_SITES)
SITE_ORDER: Final = tuple(s.value for s in ALL_SITES)

FAILED: Final = "로그인하지 못했습니다. 입력 내용을 확인해 주세요."
NEEDS_INPUT: Final = "로그인 정보가 필요합니다."
RATE_LIMITED: Final = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요."
UNAVAILABLE: Final = "이 사이트의 자동 로그인을 쓸 수 없습니다."
OTP_FAILED: Final = "인증문자 확인을 완료하지 못했습니다. 다시 시도해 주세요."
CAPTCHA_FAILED: Final = "보안문자 확인을 완료하지 못했습니다. 다시 시도해 주세요."
CAPTCHA_NOT_WAITING: Final = "보안문자 입력을 기다리고 있지 않습니다."
NO_STORE: Final = {"cache-control": "no-store"}
OTP_NOT_WAITING: Final = "인증문자 입력을 기다리고 있지 않습니다."
OTP_PATTERN: Final = r"^[0-9A-Za-z]{4,12}$"  # 인증문자 번호 형식(실제 자릿수 확인 필요)


class LoginAllRequest(BaseModel):
    """시작 로그인 창 입력. ID 1개 + 공통 암호 1개(위키 암호가 다르면 wiki_password)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    user_id: str = Field(default="", max_length=MAX_USER_ID_BYTES)
    password: str = Field(default="", max_length=MAX_SECRET_BYTES)
    wiki_password: str = Field(default="", max_length=MAX_SECRET_BYTES)  # 비우면 공통 암호
    only: SiteName | None = None  # 이 사이트만 로그인(다른 화면에서 필요할 때)
    # 이 사이트들만 로그인(필요할 때 일괄 로그인 창, 결정기록 0034). only와 함께 쓰지 않는다
    sites: list[SiteName] | None = Field(default=None, min_length=1, max_length=len(SITE_ORDER))


def account_ids(user_id: str, domain: str) -> tuple[str, str]:
    """입력 ID → (회사 계정 ID@도메인, 위키·그룹웨어 ID). '@'까지 입력했으면 그대로 쓴다."""
    plain = user_id.strip()
    if any(ch.isspace() or ord(ch) < 0x20 for ch in plain):
        raise ValueError("ID에 공백이나 제어 문자를 쓸 수 없습니다")
    if not plain:
        return "", ""
    local = plain.split("@", 1)[0]
    return (plain if "@" in plain else f"{plain}@{domain}"), local


def _not_logged_in(container: Container, sites: tuple[Site, ...]) -> list[Site]:
    return [
        site
        for site in sites
        if container.login.supports_password(site) and container.sessions.current(site) is None
    ]


def pending_sites(container: Container, only: Site | None = None) -> list[Site]:
    """로그인할 사이트 중 아직 로그인되지 않은 것.

    기본은 일괄 대상(AUTO_SITES)만. 직접 로그인 사이트(그룹웨어)는 그 사이트만 콕 집어
    요청했을 때(only)만 넣는다.
    """
    sites = AUTO_SITES + (MANUAL_SITES if only in MANUAL_SITES else ())
    return _not_logged_in(container, sites)


def _login_one(
    container: Container, site: Site, user_id: bytearray, secret: bytearray
) -> str | None:
    """사이트 1개 로그인. 성공이면 None, 실패면 화면 안내 문구."""
    company = site in COMPANY_SITES
    if not company and (not user_id or not secret):
        return NEEDS_INPUT  # 시도 횟수를 쓰지 않는다(보안 검토 L5)
    sso_only = not user_id or not secret
    # 회사 계정 사이트는 같은 계정이므로 시도 횟수를 함께 센다(보안 검토 M4).
    # 입력 없는 SSO 시도는 암호를 제출하지 않으므로 따로 센다. 진입점마다 SSO를 시도해도
    # 실제 암호 로그인 한도를 소모하지 않게 한다(결정기록 0034 보안 검토 L2)
    key = (COMPANY_SSO_LIMIT_KEY if sso_only else COMPANY_LIMIT_KEY) if company else site.value
    if container.login_limiter.check(key) is not None:
        return RATE_LIMITED
    if sso_only:
        # 입력이 없으면 SSO만 시도한다(다른 회사 계정 사이트가 로그인돼 있을 때 성공).
        if container.login.try_sso(site) is not None:
            return None
        return NEEDS_INPUT
    try:
        # 서비스가 받은 버퍼를 0으로 덮어쓰므로 사본을 넘긴다(원본은 호출자가 지운다).
        container.login.login(site, bytearray(user_id), bytearray(secret))
    except CredentialsRequiredError:
        return NEEDS_INPUT
    except OtpFailedError:
        return OTP_FAILED
    except CaptchaFailedError:  # LoginFailedError보다 먼저(하위 클래스)
        return CAPTCHA_FAILED
    except (InvalidLoginInputError, LoginFailedError):
        return FAILED
    except LoginUnavailableError:
        return UNAVAILABLE
    return None


@router.get("/pending")
def pending(request: Request, site: SiteName | None = None) -> JSONResponse:
    """입력이 필요한 계정 묶음.

    회사 계정 사이트가 하나라도 로그인돼 있으면 회사 계정은 묻지 않는다(SSO로 연결).
    site=groupware처럼 직접 로그인 사이트를 지정하면 그 사이트만 돌려준다.
    """
    container = get_container(request)
    only = None if site is None else Site(site)
    sites: list[Site]
    if only in MANUAL_SITES:
        sites = [s for s in pending_sites(container, only) if s is only]
    else:
        sites = pending_sites(container)
    company_live = any(container.sessions.current(s) is not None for s in COMPANY_SITES)
    return JSONResponse(
        {
            "sites": [s.value for s in sites],
            "need_company": any(s in COMPANY_SITES for s in sites) and not company_live,
            "need_wiki": Site.WIKI in sites,
            # 일괄 로그인에서 뺀, 사용자가 직접 로그인하는 사이트(아직 로그인 안 된 것)
            "manual_sites": [s.value for s in _not_logged_in(container, MANUAL_SITES)],
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
    if body.only is not None and body.sites is not None:
        return JSONResponse({"error": "로그인할 사이트 지정이 잘못되었습니다."}, status_code=400)
    only = None if body.only is None else Site(body.only)
    # 로그인할 사이트: only 1개 · sites 목록(직접 로그인 사이트 포함 가능) · 지정 없음(일괄 대상)
    targets: tuple[Site, ...]
    if only is not None:
        targets = tuple(pending_sites(container, only))
    elif body.sites is not None:
        wanted = {Site(s) for s in body.sites}
        targets = tuple(s for s in _not_logged_in(container, ALL_SITES) if s in wanted)
    else:
        targets = tuple(pending_sites(container))
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
        # 그룹웨어: '@도메인' 없는 아이디 + 공통 암호(위키 전용 암호를 쓰지 않는다)
        own_login = {Site.WIKI: wiki, Site.GROUPWARE: (buffers[2], buffers[1])}
        # 공통 암호를 그대로 쓰는 사이트(위키는 전용 암호를 따로 넣지 않았을 때)
        uses_common = {
            Site.GROUPWARE: True,
            Site.WIKI: hmac.compare_digest(buffers[3], buffers[1]),
        }
        # 공통 암호로 시도한 사이트가 '틀림'이거나 '한도 초과(확인 안 됨)'이면, 같은 공통 암호를
        # 다른 사이트에 다시 제출하지 않는다(계정 잠금 방지, M4·결정기록 0034 보안 검토 L1·L3)
        common_block: str | None = None
        for site in targets:
            if only is not None and site is not only:
                continue
            is_company = site in COMPANY_SITES
            common = is_company or uses_common[site]
            if common_block is not None and common:
                error: str | None = common_block
            else:
                user_id, secret = company if is_company else own_login[site]
                error = _login_one(container, site, user_id, secret)
                if common and user_id and secret and error in (FAILED, RATE_LIMITED):
                    common_block = error
            results.append({"site": site.value, "logged_in": error is None, "error": error})
    finally:
        for buffer in buffers:
            wipe(buffer)
        container.login_progress.update("")
        container.login_progress.cancel_otp()
        container.login_progress.captcha.cancel()
    # AI-Ground에 로그인됐으면 밀린 업무 제목 요약을 바로 시작한다(결정기록 0014)
    if any(r["logged_in"] and r["site"] == Site.AIGROUND.value for r in results):
        container.summary_job.kick()
    return JSONResponse({"results": results})


@router.get("/progress")
def progress(request: Request) -> JSONResponse:
    """2단계 인증 안내(승인 대기 여부·번호, 인증문자 입력 요청). 자격증명은 담지 않는다."""
    return JSONResponse(get_container(request).login_progress.snapshot(), headers=NO_STORE)


class OtpRequest(BaseModel):
    """그룹웨어 인증문자 번호. cancel=true면 입력을 취소한다."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(default="", max_length=12)
    cancel: bool = False


@router.post("/otp")
def submit_otp(request: Request, body: OtpRequest) -> JSONResponse:
    """로그인 중인 흐름에 인증문자 번호를 넘긴다. 번호는 저장·기록하지 않는다."""
    if request.url.query:
        return JSONResponse({"error": "번호는 주소에 넣을 수 없습니다."}, status_code=400)
    progress = get_container(request).login_progress
    if body.cancel:
        progress.cancel_otp()
        return JSONResponse({"accepted": True})
    if not re.fullmatch(OTP_PATTERN, body.code):
        return JSONResponse({"error": "번호 형식을 확인해 주세요."}, status_code=400)
    if not progress.submit_otp(body.code):
        return JSONResponse({"error": OTP_NOT_WAITING}, status_code=409)
    return JSONResponse({"accepted": True})


@lru_cache(maxsize=1)
def _captcha_policy() -> CaptchaPolicy:
    g = load_login_fields().groupware
    return parse_policy(g.captcha_case, g.captcha_length, g.captcha_max_tries)


class CaptchaRequest(BaseModel):
    """보안문자 입력값. cancel=true면 취소, refresh=true면 새 이미지 요청."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(default="", max_length=40)
    cancel: bool = False
    refresh: bool = False


@router.post("/captcha/image")
def take_captcha_image(request: Request) -> JSONResponse:
    """보안문자 이미지(data URL). 가져가면 버리는 동작이라 POST(CSRF 검사 대상)."""
    image = get_container(request).login_progress.captcha.take_image_data_url()
    if image is None:
        return JSONResponse({"error": CAPTCHA_NOT_WAITING}, status_code=409, headers=NO_STORE)
    return JSONResponse({"image": image}, headers=NO_STORE)


@router.post("/captcha")
def submit_captcha(request: Request, body: CaptchaRequest) -> JSONResponse:
    """보안문자 값을 로그인 중인 흐름에 넘긴다. 값은 저장·기록·응답하지 않는다."""
    if request.url.query:
        return JSONResponse({"error": "값은 주소에 넣을 수 없습니다."}, status_code=400)
    channel = get_container(request).login_progress.captcha
    if body.cancel:
        channel.cancel()
        return JSONResponse({"accepted": True}, headers=NO_STORE)
    if body.refresh:
        if not channel.refresh():
            return JSONResponse({"error": CAPTCHA_NOT_WAITING}, status_code=409, headers=NO_STORE)
        return JSONResponse({"accepted": True}, headers=NO_STORE)
    value = normalize(body.code, _captcha_policy())
    if value is None:
        return JSONResponse(
            {"error": "보안문자 형식을 확인해 주세요."}, status_code=400, headers=NO_STORE
        )
    if not channel.submit(value):
        return JSONResponse({"error": CAPTCHA_NOT_WAITING}, status_code=409, headers=NO_STORE)
    return JSONResponse({"accepted": True}, headers=NO_STORE)
