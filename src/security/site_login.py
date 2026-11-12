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

import threading
import time
from collections.abc import Callable
from typing import Any, Final
from urllib.parse import urlsplit

from security.browser import BrowserRuntime, EphemeralContext
from security.credentials import LoginFailedError
from security.login_flows import (
    EntraFields,
    LoginFields,
    WikiFields,
    run_entra_login,
    run_wiki_login,
)
from security.sessions import NativeSession

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
        return self._page.goto(url, timeout=timeout)

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


class LoginProgress:
    """2단계 인증 안내(번호)를 화면에 보여 주기 위한 메모리 상태. 자격증명은 담지 않는다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting = False
        self._number: str | None = None

    def update(self, value: str | None) -> None:
        with self._lock:
            if value == "":
                self._waiting, self._number = False, None
            else:
                self._waiting = True
                self._number = value if value and value.isdigit() else None

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {"mfa_waiting": self._waiting, "mfa_number": self._number}


class SharedContext:
    """회사 계정 사이트들이 함께 쓰는 비저장 컨텍스트.

    폐기 정책(보안 검토 M1): 묶음 중 한 사이트라도 로그아웃·유휴 만료되면 컨텍스트 전체를 닫는다.
    SSO 쿠키가 다른 사이트 세션에 남아 로그아웃한 사이트가 입력 없이 다시 열리는 일을 막는다.
    다른 사이트의 세션 항목은 SessionStore가 '닫힘'을 보고 다음 조회 때 정리한다.
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
