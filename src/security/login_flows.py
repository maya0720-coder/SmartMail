"""사이트 로그인 화면 자동 입력 흐름(AUTH-01·09, 결정기록 0011).

- 회사 계정(Microsoft Entra ID): 이메일 → 암호 → (2단계 인증 대기) → '로그인 상태 유지' 거부.
- 위키(Confluence): ID·암호 입력 → 로그인 → API로 로그인 여부 확인.
- 화면 요소는 `config/rules/login_fields.toml`에서 읽는다.
- ID·암호는 이 모듈 함수의 인자로만 잠시 지나간다. 저장·로그·예외 메시지에 넣지 않는다.
- Playwright 페이지 대신 같은 메서드를 가진 가짜 객체로 테스트한다(실제 사이트 접속 금지).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from config.rules import RULES_DIR, read_toml
from config.settings import ConfigError
from security.credentials import CredentialsRequiredError, LoginFailedError, OtpFailedError
from security.groupware_captcha import (
    CAPTCHA_FAILED,
    AskCaptcha,
    CaptchaFailedError,
    CaptchaGate,
    ErrorCategory,
    parse_policy,
    submit_with_fallback,
)

__all__ = ["CredentialsRequiredError"]  # 기존 import 경로 호환

logger = logging.getLogger(__name__)

DEFAULT_LOGIN_FIELDS: Final = RULES_DIR / "login_fields.toml"
POLL_SECONDS: Final = 0.25
# 버튼을 누른 뒤 누른 화면 요소가 사라질 때까지 기다리는 최대 시간(사라지면 바로 다음 단계)
SETTLE_SECONDS: Final = 2.0
# 그룹웨어: 제출 뒤 이 시간이 지나도 로그인 화면이면 입력이 틀린 것으로 본다(다시 제출하지 않음)
GROUPWARE_SETTLE_SECONDS: Final = 10.0
GROUPWARE_OPEN_SECONDS: Final = 10.0  # 로그인 화면의 아이디 칸이 나타날 때까지 기다리는 시간
MAX_FILLS: Final = 3  # 같은 칸을 이보다 많이 채워야 하면 입력값이 틀린 것으로 본다
MAX_OTP_TRIES: Final = 3  # 인증문자 번호 입력 허용 횟수

WRONG_INPUT: Final = "로그인하지 못했습니다. 입력 내용을 확인해 주세요."
TIMEOUT: Final = "로그인 시간이 초과됐습니다. 다시 시도해 주세요."
CREDENTIALS_REQUIRED: Final = "로그인 정보가 필요합니다."
GENERIC_PAGE_ERROR: Final = "로그인 화면이 예상과 달라 입력하지 않았습니다."
OTP_FAILED: Final = "인증문자 확인을 완료하지 못했습니다."

# 인증문자 번호 요청: (재입력 여부, 남은 초) → 번호. 취소·시간 초과면 None
AskOtp = Callable[[bool, float], "str | None"]


def _wait_gone(
    page: LoginPage,
    selector: str,
    now: Callable[[], float],
    sleep: Callable[[float], None],
    limit: float = SETTLE_SECONDS,
) -> None:
    """방금 조작한 화면 요소가 사라질 때까지(최대 limit초) 짧게 확인하며 기다린다."""
    until = now() + limit
    while now() < until and page.visible(selector):
        sleep(POLL_SECONDS)


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
    account_domain: str
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
class GroupwareFields:
    user: str
    concealed_placeholder: str
    concealed_input: str
    otp_input: str
    login_path_marker: str
    captcha_image: str
    captcha_input: str
    captcha_refresh: str
    submit: str
    error_area: str
    captcha_case: str
    captcha_length: str
    captcha_max_tries: str


class FormLoginPage(LoginPage, Protocol):
    """자체 로그인 폼(그룹웨어)에 더 필요한 부분: 키 입력, 알림창 발생 횟수.

    dialog_count: 일반 알림창(암호 오류 등). otp_notice_count: 인증번호 발송·불일치 안내 알림창.
    """

    def press(self, selector: str, key: str) -> None: ...

    @property
    def dialog_count(self) -> int: ...

    @property
    def otp_notice_count(self) -> int: ...

    # 보안문자 지원(결정기록 0025): 불일치 안내 횟수, 화면 이동 횟수, 마지막 오류 범주
    @property
    def captcha_notice_count(self) -> int: ...

    @property
    def navigation_count(self) -> int: ...

    @property
    def last_error_category(self) -> ErrorCategory: ...

    def capture(self, selector: str) -> bytes | None: ...

    def attribute(self, selector: str, name: str) -> str: ...

    def has_value(self, selector: str) -> bool: ...

    def hidden_names(self) -> list[str]: ...

    def error_category(self, selector: str) -> ErrorCategory | None: ...


@dataclass(frozen=True, slots=True)
class LoginFields:
    entra: EntraFields
    site_logged_in: dict[str, str]
    wiki: WikiFields
    groupware: GroupwareFields


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
        _section(data, "entra", EntraFields),
        logged_in,
        _section(data, "wiki", WikiFields),
        _section(data, "groupware", GroupwareFields),
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
            acted: str | None = None  # 조작한 화면 요소(사라질 때까지 기다린다)
            if host == fields.host:
                if page.visible(fields.error):
                    raise LoginFailedError(WRONG_INPUT)
                if page.visible(fields.kmsi_marker):
                    page.click(fields.kmsi_no)
                    acted = fields.kmsi_marker
                elif page.visible(fields.concealed_input):
                    # 암호는 한 번만 제출한다. 다시 보이면 화면 전환을 기다리고, 틀리면 오류 표시로
                    # 실패 처리된다(계정 잠금 위험을 줄임, 보안 검토 M4).
                    if fills["concealed"] == 0:
                        fill("concealed", fields.concealed_input, secret)
                        acted = fields.concealed_input
                elif page.visible(fields.email):
                    fill("email", fields.email, user_id)
                    acted = fields.email
                elif page.visible(fields.mfa_number):
                    on_mfa(page.text(fields.mfa_number).strip()[:4] or None)
                elif page.visible(fields.mfa_marker):
                    on_mfa(None)
                elif page.visible(fields.account_tile):
                    page.click(fields.account_tile)
                    acted = fields.account_tile
            if acted:
                _wait_gone(page, acted, now, sleep)
            else:
                sleep(POLL_SECONDS)
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


def run_groupware_login(
    page: FormLoginPage,
    base_url: str,
    user_id: str,
    secret: str,
    fields: GroupwareFields,
    *,
    deadline: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
    ask_otp: AskOtp,
    ask_captcha: AskCaptcha | None = None,
) -> None:
    """그룹웨어 자체 로그인(ID는 '@도메인' 없는 아이디). 암호는 한 번만 제출한다.

    성공: 같은 사이트에서 주소 경로가 로그인 화면을 벗어남.
    실패: 알림창이 뜸, 일정 시간 뒤에도 로그인 화면에 머묾.
    추가 인증(인증문자) 입력칸이 나오면 ask_otp로 사용자에게 번호를 받아 넣는다(결정기록 0020).
    보안문자 이미지가 보이면 같은 화면에서 캡처해 ask_captcha로 값을 받아 넣는다(결정기록 0025).
    화면을 다시 불러오거나 폼 밖으로 따로 전송하지 않으므로 csrf·captchaKey 같은 hidden 값과
    세션 쿠키는 화면이 낸 그대로 함께 제출된다.
    """
    if not user_id or not secret:
        raise CredentialsRequiredError(CREDENTIALS_REQUIRED)
    page.goto(base_url)
    expected = urlsplit(base_url)
    marker = fields.login_path_marker.lower()

    def on_site() -> bool:
        current = urlsplit(page.url)
        return current.scheme == "https" and current.netloc == expected.netloc

    def still_on_login_page() -> bool:
        return on_site() and marker in urlsplit(page.url).path.lower()

    # 화면을 다 불러오기 전에 확인을 시작하므로 아이디 칸이 나타날 때까지 잠시 기다린다.
    # 다른 호스트로 넘어갔거나 로그인 화면이 아니면 입력하지 않는다(위키와 같은 규칙).
    open_until = min(deadline, now() + GROUPWARE_OPEN_SECONDS)
    while not (still_on_login_page() and page.visible(fields.user)):
        if now() >= open_until:
            raise LoginFailedError(GENERIC_PAGE_ERROR)
        sleep(POLL_SECONDS)

    page.fill(fields.user, user_id)
    if page.visible(fields.concealed_placeholder):
        page.click(fields.concealed_placeholder)  # 자리표시 칸을 누르면 실제 암호칸이 나온다
    # 암호를 넣기 직전과 제출 직전에 주소를 다시 확인한다(그 사이 다른 곳으로 넘어갔으면 중단)
    if not still_on_login_page():
        raise LoginFailedError(GENERIC_PAGE_ERROR)
    page.fill(fields.concealed_input, secret)
    if not still_on_login_page():
        raise LoginFailedError(GENERIC_PAGE_ERROR)
    policy = parse_policy(fields.captcha_case, fields.captcha_length, fields.captcha_max_tries)
    gate = CaptchaGate(page, fields, policy, ask_captcha, now=now, sleep=sleep, deadline=deadline)
    if gate.present():  # 보안문자가 처음부터 보이면 제출 전에 사용자에게 받는다
        gate.solve(False)
        if not still_on_login_page():
            raise LoginFailedError(GENERIC_PAGE_ERROR)

    def watch() -> tuple[object, ...]:
        """제출됐는지 판단할 화면 상태(주소·이동·알림창·입력칸·보안문자 이미지)."""
        return (
            page.url,
            page.navigation_count,
            page.dialog_count,
            page.otp_notice_count,
            page.captcha_notice_count,
            page.visible(fields.otp_input),
            page.attribute(fields.captcha_image, "src"),
        )

    def submit(selector: str) -> None:
        submit_with_fallback(page, selector, fields.submit, watch, now=now, sleep=sleep)

    # 화면을 열 때 뜬 공지 알림창은 실패로 세지 않는다
    dialogs_before, notices_before = page.dialog_count, page.otp_notice_count
    captcha_before = page.captcha_notice_count
    submit(fields.concealed_input)
    nav_at_submit = (
        page.navigation_count
    )  # 제출 뒤 화면이 새로 불러와졌는지(오류 영역이 남은 글자인지) 구분
    settle_until = now() + GROUPWARE_SETTLE_SECONDS
    otp_tries = 0
    resubmits = 0  # 보안문자 때문에 폼을 다시 제출한 횟수
    logged_category: ErrorCategory | None = None
    while now() < deadline:
        # 인증문자 칸이 보이면 주소와 관계없이 로그인 전이다(인증 화면 주소에 login이 없을 수 있음)
        otp_visible = on_site() and page.visible(fields.otp_input)
        if not otp_visible and on_site() and marker not in urlsplit(page.url).path.lower():
            return
        new_dialog = page.dialog_count > dialogs_before
        otp_notice = page.otp_notice_count > notices_before  # 인증번호 발송·불일치 안내
        area = page.error_category(fields.error_area) if on_site() else None
        if area is not None and area is not logged_category:
            logged_category = area  # 원문 없이 범주만 남긴다
            logger.info("그룹웨어 로그인 화면 오류 범주=%s", area.value)
        # 아이디·암호 오류가 분명하면 보안문자 재제출 없이 바로 중단한다(암호 1회 제출 원칙, M4)
        if otp_tries == 0 and (
            area is ErrorCategory.CREDENTIALS
            or (new_dialog and page.last_error_category is ErrorCategory.CREDENTIALS)
        ):
            raise LoginFailedError(WRONG_INPUT)
        # 보안문자 재요청은 명확한 반려 신호(불일치 범주·이미지 재발급)일 때만 한다
        if not otp_visible and on_site() and gate.present():
            reloaded = page.navigation_count != nav_at_submit
            rejected = (
                page.captcha_notice_count > captcha_before
                or (area is ErrorCategory.CAPTCHA and reloaded)
                or (gate.solved > 0 and gate.image_changed())
            )
            if rejected:
                if resubmits >= gate.max_tries:  # 폼을 다시 제출하는 횟수 상한
                    raise CaptchaFailedError(CAPTCHA_FAILED)
                gate.require_filled()
                gate.solve(True)
                if not still_on_login_page():
                    raise LoginFailedError(GENERIC_PAGE_ERROR)
                dialogs_before, notices_before = page.dialog_count, page.otp_notice_count
                captcha_before = page.captcha_notice_count
                resubmits += 1
                submit(fields.captcha_input)
                nav_at_submit = page.navigation_count
                settle_until = now() + GROUPWARE_SETTLE_SECONDS
                continue
        if otp_tries == 0 and new_dialog:
            # 번호를 묻기 전에 뜬 일반 알림창은 아이디·암호 오류다(인증 칸이 미리 보여도 우선)
            raise LoginFailedError(WRONG_INPUT)
        # 인증문자 화면: 처음 나왔을 때, 번호 제출 뒤 안내가 떴거나 한참 그대로일 때 번호를 받는다
        if otp_visible and (otp_tries == 0 or new_dialog or otp_notice or now() >= settle_until):
            if otp_tries >= MAX_OTP_TRIES:
                raise OtpFailedError(OTP_FAILED)
            code = ask_otp(otp_tries > 0, max(0.0, deadline - now()))
            if not code:
                raise OtpFailedError(OTP_FAILED)  # 취소·시간 초과
            if not on_site():
                raise LoginFailedError(GENERIC_PAGE_ERROR)
            page.fill(fields.otp_input, code)
            page.press(fields.otp_input, "Enter")
            otp_tries += 1
            dialogs_before, notices_before = page.dialog_count, page.otp_notice_count
            settle_until = now() + GROUPWARE_SETTLE_SECONDS
        elif otp_tries == 0:
            if on_site() and now() >= settle_until:
                # 인증번호 안내는 떴는데 입력칸을 찾지 못함: 인증문자 실패로 구분한다
                raise (OtpFailedError(OTP_FAILED) if otp_notice else LoginFailedError(WRONG_INPUT))
        sleep(POLL_SECONDS)
    if otp_tries:
        raise OtpFailedError(OTP_FAILED)
    raise LoginFailedError(TIMEOUT)
