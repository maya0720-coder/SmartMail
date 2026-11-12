"""사이트 로그인 화면 자동 입력 흐름(AUTH-01·09, 결정기록 0011).

- 회사 계정(Microsoft Entra ID): 이메일 → 암호 → (2단계 인증 대기) → '로그인 상태 유지' 거부.
- 위키(Confluence): ID·암호 입력 → 로그인 → API로 로그인 여부 확인.
- 화면 요소는 `config/rules/login_fields.toml`에서 읽는다.
- ID·암호는 이 모듈 함수의 인자로만 잠시 지나간다. 저장·로그·예외 메시지에 넣지 않는다.
- Playwright 페이지 대신 같은 메서드를 가진 가짜 객체로 테스트한다(실제 사이트 접속 금지).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from config.rules import RULES_DIR, read_toml
from config.settings import ConfigError
from security.credentials import CredentialsRequiredError, LoginFailedError

__all__ = ["CredentialsRequiredError"]  # 기존 import 경로 호환

DEFAULT_LOGIN_FIELDS: Final = RULES_DIR / "login_fields.toml"
POLL_SECONDS: Final = 0.5
SETTLE_SECONDS: Final = 2.0  # 버튼을 누른 뒤 다음 화면으로 바뀔 때까지 기다리는 시간
MAX_FILLS: Final = 3  # 같은 칸을 이보다 많이 채워야 하면 입력값이 틀린 것으로 본다

WRONG_INPUT: Final = "로그인하지 못했습니다. 입력 내용을 확인해 주세요."
TIMEOUT: Final = "로그인 시간이 초과됐습니다. 다시 시도해 주세요."
CREDENTIALS_REQUIRED: Final = "로그인 정보가 필요합니다."
GENERIC_PAGE_ERROR: Final = "로그인 화면이 예상과 달라 입력하지 않았습니다."


class LoginPage(Protocol):
    """Playwright Page 중 로그인 흐름이 쓰는 부분."""

    @property
    def url(self) -> str: ...

    def goto(self, url: str, timeout: float = ...) -> Any: ...

    def visible(self, selector: str) -> bool: ...

    def fill(self, selector: str, value: str) -> None: ...

    def click(self, selector: str) -> None: ...

    def text(self, selector: str) -> str: ...

    def get_json(self, url: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class EntraFields:
    host: str
    email: str
    concealed_input: str
    next: str
    kmsi_marker: str
    kmsi_no: str
    mfa_number: str
    mfa_marker: str
    error: str
    account_tile: str


@dataclass(frozen=True, slots=True)
class WikiFields:
    path: str
    user: str
    concealed_input: str
    submit: str
    error: str
    current_user_api: str


@dataclass(frozen=True, slots=True)
class LoginFields:
    entra: EntraFields
    site_logged_in: dict[str, str]
    wiki: WikiFields


def _section(data: dict[str, object], name: str, cls: type[Any]) -> Any:
    raw = data.get(name)
    keys = set(cls.__dataclass_fields__)
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ConfigError(f"login_fields: [{name}] 키 형식 오류")
    if not all(isinstance(v, str) and v.strip() for v in raw.values()):
        raise ConfigError(f"login_fields: [{name}] 값은 비어 있지 않은 문자열이어야 합니다")
    return cls(**{k: str(v).strip() for k, v in raw.items()})


def load_login_fields(path: Path = DEFAULT_LOGIN_FIELDS) -> LoginFields:
    data = read_toml(path)
    sites = data.get("sites", {})
    if not isinstance(sites, dict):
        raise ConfigError("login_fields: [sites] 형식 오류")
    logged_in: dict[str, str] = {}
    for site, value in sites.items():
        marker = value.get("logged_in") if isinstance(value, dict) else None
        if not isinstance(marker, str) or not marker.strip():
            raise ConfigError("login_fields: sites.*.logged_in 형식 오류")
        logged_in[str(site)] = marker.strip()
    return LoginFields(
        _section(data, "entra", EntraFields), logged_in, _section(data, "wiki", WikiFields)
    )


def run_entra_login(
    page: LoginPage,
    start_url: str,
    logged_in_marker: str,
    user_id: str,
    secret: str,
    fields: EntraFields,
    *,
    deadline: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
    on_mfa: Callable[[str | None], None],
) -> None:
    """회사 계정 로그인. 이미 SSO로 로그인돼 있으면 입력 없이 끝난다.

    user_id·secret이 빈 값이면 SSO만 시도하고, 입력 화면이 나오면 CredentialsRequiredError.
    on_mfa: 2단계 인증 번호(str), 번호 없는 승인 대기(None), 안내 지움("")을 알린다.
    """
    target_host = urlsplit(start_url).hostname
    page.goto(start_url)
    fills = {"email": 0, "concealed": 0}

    def fill(kind: str, selector: str, value: str) -> None:
        if not value:
            raise CredentialsRequiredError(CREDENTIALS_REQUIRED)
        fills[kind] += 1
        if fills[kind] > MAX_FILLS:
            raise LoginFailedError(WRONG_INPUT)
        page.fill(selector, value)
        page.click(fields.next)

    try:
        while now() < deadline:
            host = urlsplit(page.url).hostname
            if host == target_host and page.visible(logged_in_marker):
                return
            acted = False
            if host == fields.host:
                if page.visible(fields.error):
                    raise LoginFailedError(WRONG_INPUT)
                if page.visible(fields.kmsi_marker):
                    page.click(fields.kmsi_no)
                    acted = True
                elif page.visible(fields.concealed_input):
                    # 암호는 한 번만 제출한다. 다시 보이면 화면 전환을 기다리고, 틀리면 오류 표시로
                    # 실패 처리된다(계정 잠금 위험을 줄임, 보안 검토 M4).
                    if fills["concealed"] == 0:
                        fill("concealed", fields.concealed_input, secret)
                        acted = True
                elif page.visible(fields.email):
                    fill("email", fields.email, user_id)
                    acted = True
                elif page.visible(fields.mfa_number):
                    on_mfa(page.text(fields.mfa_number).strip()[:4] or None)
                elif page.visible(fields.mfa_marker):
                    on_mfa(None)
                elif page.visible(fields.account_tile):
                    page.click(fields.account_tile)
                    acted = True
            sleep(SETTLE_SECONDS if acted else POLL_SECONDS)
        raise LoginFailedError(TIMEOUT)
    finally:
        on_mfa("")  # 2단계 인증 안내 지움


def run_wiki_login(
    page: LoginPage,
    base_url: str,
    user_id: str,
    secret: str,
    fields: WikiFields,
    *,
    deadline: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """위키 자체 로그인. 로그인 API가 known 사용자를 돌려줘야 성공이다."""
    if not user_id or not secret:
        raise CredentialsRequiredError(CREDENTIALS_REQUIRED)
    base = base_url.rstrip("/")
    page.goto(base + fields.path)
    # 다른 호스트로 넘어갔으면 입력하지 않는다(보안 검토 L1)
    expected, landed = urlsplit(base), urlsplit(page.url)
    if landed.scheme != "https" or landed.netloc != expected.netloc:
        raise LoginFailedError(GENERIC_PAGE_ERROR)
    page.fill(fields.user, user_id)
    page.fill(fields.concealed_input, secret)
    page.click(fields.submit)
    while now() < deadline:
        if page.visible(fields.error):
            raise LoginFailedError(WRONG_INPUT)
        if fields.path not in page.url:
            info = page.get_json(base + fields.current_user_api)
            if isinstance(info, dict) and info.get("type") == "known":
                return
            raise LoginFailedError(WRONG_INPUT)
        sleep(POLL_SECONDS)
    raise LoginFailedError(TIMEOUT)
