"""ID·암호로 사이트 로그인 화면을 자동으로 채우는 어댑터(AUTH-01·03·09, 결정기록 0011).

- 회사 계정(Entra ID) 사이트(AI-Ground·G-Knowledge)는 브라우저 컨텍스트 1개를 함께 쓴다.
  한 사이트에 로그인하면 다른 사이트는 SSO로 입력 없이 로그인된다.
  사이트별 세션은 '임대(lease)'로 나눠 주고, 모든 임대가 닫히면 컨텍스트를 닫는다.
- 위키는 자체 로그인 화면이라 별도 컨텍스트를 쓴다.
- ID·암호는 bytearray로 받아 브라우저 스레드에서 입력할 때만 문자열로 바꾼다.
  호출이 끝나면 LoginService가 버퍼를 0으로 덮어쓴다(문자열 사본 한계: 결정기록 0005).
- 모든 컨텍스트는 비저장(시크릿과 같음)이고 화면 없이(headless) 실행한다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Final
from urllib.parse import urlsplit

from security.browser import BrowserRuntime, EphemeralContext
from security.captcha_channel import CaptchaChannel
from security.credentials import LoginFailedError
from security.groupware_page import CaptchaPageMixin
from security.login_flows import (
    EntraFields,
    GroupwareFields,
    LoginFields,
    WikiFields,
    run_entra_login,
    run_groupware_login,
    run_wiki_login,
)
from security.sessions import NativeSession

logger = logging.getLogger(__name__)

LOGIN_WAIT_SECONDS: Final = 180.0  # 2단계 인증 승인 대기 포함
CALL_MARGIN_SECONDS: Final = 15.0
PAGE_TIMEOUT_MS: Final = 30_000
ACTION_TIMEOUT_MS: Final = 10_000
GENERIC_FAILURE: Final = "로그인하지 못했습니다"


class PlaywrightLoginPage:
    """Playwright Page를 로그인 흐름이 쓰는 최소 메서드로 감싼다."""

    def __init__(self, page: Any, context: Any) -> None:
        self._page = page
        self._context = context

    @property
    def url(self) -> str:
        return str(self._page.url)

    def goto(self, url: str, timeout: float = PAGE_TIMEOUT_MS) -> Any:
        # 이미지·광고 등 모든 리소스를 기다리지 않고 문서가 준비되면 바로 진행한다(로그인 속도).
        # 로그인 흐름이 필요한 화면 요소가 나타날 때까지 따로 확인한다.
        return self._page.goto(url, timeout=timeout, wait_until="domcontentloaded")

    def visible(self, selector: str) -> bool:
        try:
            return bool(self._page.locator(selector).first.is_visible())
        except Exception:
            return False

    def fill(self, selector: str, value: str) -> None:
        self._page.locator(selector).first.fill(value, timeout=ACTION_TIMEOUT_MS)

    def click(self, selector: str) -> None:
        self._page.locator(selector).first.click(timeout=ACTION_TIMEOUT_MS)

    def text(self, selector: str) -> str:
        return str(self._page.locator(selector).first.inner_text(timeout=ACTION_TIMEOUT_MS))

    def get_json(self, url: str) -> Any:
        response = self._context.request.get(url, timeout=PAGE_TIMEOUT_MS, max_redirects=0)
        return response.json() if response.ok else None


class PlaywrightFormLoginPage(CaptchaPageMixin, PlaywrightLoginPage):
    """자체 로그인 폼용: 키 입력과 알림창 횟수(보안문자 지원은 CaptchaPageMixin).

    알림창 글자는 저장·기록하지 않고, 인증번호 안내인지 판별하는 데만 쓴다.
    인증번호 안내(발송 확인 등)는 '확인'을, 그 밖의 알림창은 '취소'를 누른다.
    """

    def __init__(self, page: Any, context: Any) -> None:
        super().__init__(page, context)
        self._dialogs = 0
        self._otp_notices = 0
        self._init_captcha(page)  # 이동 감지는 알림창 처리기보다 먼저 등록한다
        page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog: Any) -> None:
        if self._captcha_dialog(dialog):  # 보안문자 불일치 안내는 별도로 센다
            return
        # 확인(confirm)·입력(prompt) 창은 수락하지 않는다: 안내(alert)만 인증번호 안내로 본다
        if str(getattr(dialog, "type", "")) == "alert" and is_otp_notice(str(dialog.message or "")):
            self._otp_notices += 1
            dialog.accept()
        else:
            self._dialogs += 1
            dialog.dismiss()

    @property
    def dialog_count(self) -> int:
        return self._dialogs

    @property
    def otp_notice_count(self) -> int:
        return self._otp_notices

    def press(self, selector: str, key: str) -> None:
        self._page.locator(selector).first.press(key, timeout=ACTION_TIMEOUT_MS)

    def describe(self) -> str:
        """실패 원인 확인용 화면 구조 요약: 주소 경로, 보이는 입력칸·버튼의 id/name/type, 프레임 수.

        입력값·글자·쿼리 문자열은 담지 않는다(결정기록 0010과 같은 기준).
        """
        try:
            items = self._page.evaluate(_DESCRIBE_JS)
            frames = len(self._page.frames)
        except Exception:
            return "화면 구조를 읽지 못함"
        path = safe_path(urlsplit(self.url).path)
        shown = ", ".join(_printable(str(i)[:60]) for i in list(items)[:40])
        return f"path={path} frames={frames} visible=[{shown}]"


# '문자' 단독은 '특수문자' 같은 암호 안내와 겹치므로 쓰지 않는다
OTP_NOTICE_WORDS: Final = ("인증번호", "인증 번호", "인증문자", "문자 인증", "otp", "sms")

# 보이는 input·button·select의 태그·type·id·name만 모은다(값·글자는 읽지 않음)
_DESCRIBE_JS: Final = """() => Array.from(document.querySelectorAll('input,button,select,textarea'))
  .filter(e => e.offsetParent !== null && e.type !== 'hidden')
  .map(e => [e.tagName.toLowerCase(), e.type === 'password' ? 'secret' : (e.type || ''),
             e.id ? '#' + e.id : '',
             e.name ? 'name=' + e.name : ''].filter(Boolean).join(' '))"""


def safe_path(path: str) -> str:
    """로그용 경로: ';jsessionid=' 같은 경로 매개변수를 자르고 영문·숫자·/._- 외는 지운다."""
    kept = path.split(";", 1)[0][:120]
    return "".join(ch for ch in kept if ch.isascii() and (ch.isalnum() or ch in "/._-"))


def _printable(text: str) -> str:
    """로그 한 줄을 깨뜨리는 줄바꿈·제어 문자를 지운다(화면 값으로 로그 위조 방지)."""
    return "".join(ch for ch in text if ch.isprintable())


def is_otp_notice(message: str) -> bool:
    """알림창이 인증번호 발송·확인 안내인지. 글자는 이 판별에만 쓰고 버린다."""
    lowered = message.lower()
    return any(word in lowered for word in OTP_NOTICE_WORDS)


class LoginProgress:
    """2단계 인증 안내(번호)와 인증문자 번호 전달을 위한 메모리 상태.

    인증문자 번호는 로그인 흐름이 꺼내 가는 즉시 지운다. 로그·응답에 넣지 않는다.
    ID·암호는 담지 않는다.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._waiting = False
        self._number: str | None = None
        self._otp_waiting = False
        self._otp_retry = False
        self._otp_code: str | None = None
        self._otp_cancelled = False
        self.captcha = CaptchaChannel()  # 그룹웨어 보안문자 이미지·입력(결정기록 0025)

    def update(self, value: str | None) -> None:
        with self._cond:
            if value == "":
                self._waiting, self._number = False, None
            else:
                self._waiting = True
                self._number = value if value and value.isdigit() else None

    def snapshot(self) -> dict[str, object]:
        with self._cond:
            return {
                "mfa_waiting": self._waiting,
                "mfa_number": self._number,
                "otp_waiting": self._otp_waiting,
                "otp_retry": self._otp_retry,
                # kind: 화면이 받아야 할 입력 종류(otp | captcha | None). 기존 필드는 유지
                "kind": "captcha"
                if self.captcha.snapshot()["captcha_waiting"]
                else ("otp" if self._otp_waiting else None),
                **self.captcha.snapshot(),
            }

    def ask_otp(self, retry: bool, timeout: float) -> str | None:
        """화면에 인증문자 입력을 요청하고 번호를 기다린다. 취소·시간 초과면 None."""
        with self._cond:
            self._otp_waiting, self._otp_retry = True, retry
            self._otp_code, self._otp_cancelled = None, False
            self._cond.wait_for(
                lambda: self._otp_code is not None or self._otp_cancelled, timeout=timeout
            )
            code, self._otp_code = self._otp_code, None
            self._otp_waiting = self._otp_retry = False
            return code

    def submit_otp(self, code: str) -> bool:
        """화면에서 받은 번호를 넘긴다. 입력을 기다리는 중이 아니면 False."""
        with self._cond:
            if not self._otp_waiting:
                return False
            self._otp_code = code
            self._otp_waiting = False
            self._cond.notify_all()
            return True

    def cancel_otp(self) -> None:
        with self._cond:
            self._otp_cancelled = True
            self._otp_waiting = self._otp_retry = False
            self._otp_code = None
            self._cond.notify_all()


class SharedContext:
    """회사 계정 사이트들이 함께 쓰는 비저장 컨텍스트.

    폐기 정책(보안 검토 M1): 묶음 중 한 사이트라도 로그아웃·만료되면 컨텍스트 전체를 닫는다.
    SSO 쿠키가 다른 사이트 세션에 남아 로그아웃한 사이트가 입력 없이 다시 열리는 일을 막는다.
    다른 사이트의 세션 항목은 SessionStore가 '닫힘'을 보고 다음 조회 때 정리한다(CASCADE).
    유휴 시계는 묶음 단위로 세고(SessionStore가 `SharedLease.group`으로 판별), 같은 컨텍스트로
    다시 로그인할 때는 이전 임대를 `detach()`로 정리해 컨텍스트를 닫지 않는다(결정기록 0031).
    """

    def __init__(self, context: EphemeralContext) -> None:
        self.context = context
        self._lock = threading.Lock()
        self._discarded = False

    @property
    def closed(self) -> bool:
        return self._discarded or self.context.closed

    def lease(self) -> SharedLease:
        with self._lock:
            if self.closed:
                raise LoginFailedError(GENERIC_FAILURE)  # 닫힌 컨텍스트로 세션을 만들지 않음(M3)
            return SharedLease(self)

    def discard(self) -> None:
        with self._lock:
            self._discarded = True
        self.context.close()


class SharedLease(EphemeralContext):
    """사이트 1개의 세션. 연동 어댑터에는 일반 브라우저 컨텍스트처럼 보인다."""

    def __init__(self, shared: SharedContext) -> None:  # noqa: D107 - 부모 생성자를 쓰지 않음
        self._shared: SharedContext | None = shared
        self._group = shared  # 묶음 식별용(같은 객체면 같은 묶음). 닫거나 끊어도 유지

    @property
    def group(self) -> object:
        return self._group

    def detach(self) -> None:
        """참조만 끊는다. 공용 컨텍스트는 다른 임대가 계속 쓰므로 닫지 않는다(재로그인 교체용)."""
        self._shared = None

    @property
    def closed(self) -> bool:
        return self._shared is None or self._shared.closed

    def call(self, func: Callable[[Any], Any], timeout: float | None = None) -> Any:
        shared = self._shared
        if shared is None or shared.closed:
            raise RuntimeError("브라우저 컨텍스트가 닫혔습니다")
        return shared.context.call(func, timeout)

    def close(self) -> None:
        shared, self._shared = self._shared, None
        if shared is not None:
            shared.discard()


class EntraLoginGroup:
    """회사 계정 사이트 묶음. 공용 컨텍스트 1개로 순서대로 로그인한다."""

    def __init__(
        self,
        runtime: BrowserRuntime,
        fields: LoginFields,
        progress: LoginProgress,
        wait_seconds: float = LOGIN_WAIT_SECONDS,
    ) -> None:
        self._runtime = runtime
        self._entra: EntraFields = fields.entra
        self._markers = fields.site_logged_in
        self._progress = progress
        self._wait = wait_seconds
        self._lock = threading.Lock()
        self._shared: SharedContext | None = None

    def login(
        self, site_name: str, start_url: str, user_id: bytearray, secret: bytearray
    ) -> NativeSession:
        marker = self._markers.get(site_name)
        if marker is None:
            raise LoginFailedError(GENERIC_FAILURE)
        with self._lock:
            shared = self._shared
            created = shared is None or shared.closed
            if shared is None or shared.closed:
                shared = SharedContext(self._runtime.open_context(headless=True))
            deadline = time.monotonic() + self._wait

            def drive(raw: Any) -> None:
                page = raw.new_page()
                try:
                    run_entra_login(
                        PlaywrightLoginPage(page, raw),
                        start_url,
                        marker,
                        user_id.decode("utf-8"),
                        secret.decode("utf-8"),
                        self._entra,
                        deadline=deadline,
                        now=time.monotonic,
                        sleep=time.sleep,
                        on_mfa=self._progress.update,
                    )
                finally:
                    page.close()

            try:
                shared.context.call(drive, timeout=self._wait + CALL_MARGIN_SECONDS)
            except Exception:
                self._progress.update("")
                if created:
                    shared.discard()  # 새로 만든 컨텍스트는 아직 쓰는 세션이 없다
                raise
            self._shared = shared
            return shared.lease()


class EntraPasswordAuthenticator:
    """회사 계정 사이트 1개의 PasswordAuthenticator. 빈 입력이면 SSO만 시도한다."""

    supports_sso = True

    def __init__(self, group: EntraLoginGroup, site_name: str, start_url: str) -> None:
        self._group = group
        self._site_name = site_name
        self._start_url = start_url

    def authenticate(self, user_id: bytearray, secret: bytearray) -> NativeSession:
        return self._group.login(self._site_name, self._start_url, user_id, secret)


def site_origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class WikiPasswordAuthenticator:
    """위키(Confluence) 자체 로그인 화면을 채운다."""

    supports_sso = False

    def __init__(
        self,
        runtime: BrowserRuntime,
        base_url: str,
        fields: WikiFields,
        wait_seconds: float = 60.0,
    ) -> None:
        self._runtime = runtime
        self._origin = site_origin(base_url)
        self._fields = fields
        self._wait = wait_seconds

    def authenticate(self, user_id: bytearray, secret: bytearray) -> NativeSession:
        context = self._runtime.open_context(headless=True)
        deadline = time.monotonic() + self._wait

        def drive(raw: Any) -> None:
            page = raw.new_page()
            try:
                run_wiki_login(
                    PlaywrightLoginPage(page, raw),
                    self._origin,
                    user_id.decode("utf-8"),
                    secret.decode("utf-8"),
                    self._fields,
                    deadline=deadline,
                    now=time.monotonic,
                    sleep=time.sleep,
                )
            finally:
                page.close()

        try:
            context.call(drive, timeout=self._wait + CALL_MARGIN_SECONDS)
        except Exception:
            context.close()
            raise
        return context


class GroupwarePasswordAuthenticator:
    """그룹웨어 자체 로그인 화면을 채운다(ID는 '@도메인' 없는 아이디).

    위키처럼 별도 비저장 컨텍스트를 쓴다. 인증문자를 요구하면 progress로 화면에 번호를 받는다.
    """

    supports_sso = False

    def __init__(
        self,
        runtime: BrowserRuntime,
        base_url: str,
        fields: GroupwareFields,
        progress: LoginProgress,
        wait_seconds: float = LOGIN_WAIT_SECONDS,  # 인증문자 수신·입력 시간 포함
    ) -> None:
        self._runtime = runtime
        self._base_url = base_url
        self._fields = fields
        self._progress = progress
        self._wait = wait_seconds

    def authenticate(self, user_id: bytearray, secret: bytearray) -> NativeSession:
        context = self._runtime.open_context(headless=True)
        deadline = time.monotonic() + self._wait

        def drive(raw: Any) -> None:
            page = raw.new_page()
            form = PlaywrightFormLoginPage(page, raw)
            try:
                run_groupware_login(
                    form,
                    self._base_url,
                    user_id.decode("utf-8"),
                    secret.decode("utf-8"),
                    self._fields,
                    deadline=deadline,
                    now=time.monotonic,
                    sleep=time.sleep,
                    ask_otp=self._progress.ask_otp,
                    ask_captcha=self._progress.captcha.ask,
                )
            except LoginFailedError as exc:
                # 실패 원인 확인용: 화면 구조만 기록한다(ID·암호·번호·알림창 글자 없음)
                logger.warning(
                    "그룹웨어 로그인 실패(%s) 알림창=%d 인증안내=%d 보안문자안내=%d 오류범주=%s %s",
                    type(exc).__name__,
                    form.dialog_count,
                    form.otp_notice_count,
                    form.captcha_notice_count,
                    form.last_error_category.value,
                    form.describe(),
                )
                raise
            finally:
                page.close()

        try:
            context.call(drive, timeout=self._wait + CALL_MARGIN_SECONDS)
        except Exception:
            context.close()
            raise
        finally:
            self._progress.cancel_otp()  # 화면의 번호 입력 요청을 지운다
            self._progress.captcha.cancel()  # 보안문자 이미지·입력 요청도 지운다
        return context
