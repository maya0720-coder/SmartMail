"""비저장 브라우저 자동화 실행기(AUTH-06, AUTH-09, GK-04).

- Playwright의 비영속 컨텍스트(시크릿 창과 같음)만 쓴다. `launch_persistent_context`는 쓰지 않는다.
- 브라우저 프로세스의 임시 폴더(TMP/TEMP)를 앱 전용 임시 폴더로 지정하고, 종료 시 폴더째 지운다.
- Playwright 동기 API는 만든 스레드에서만 쓸 수 있으므로, 모든 호출을 전용 스레드 하나에서 실행한다.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final, Protocol, TypeVar
from urllib.parse import urlsplit

from security.credentials import LoginFailedError
from security.sessions import NativeSession

logger = logging.getLogger(__name__)

TEMP_PREFIX: Final = "smartmail-pw-"
DEFAULT_LOGIN_WAIT_SECONDS: Final = 300
_TEMP_ENV_KEYS: Final = ("TMP", "TEMP", "TMPDIR")

T = TypeVar("T")


class RawContext(Protocol):
    def close(self) -> None: ...


class BrowserBackend(Protocol):
    def new_context(self, headless: bool) -> RawContext: ...

    def close(self) -> None: ...


class PlaywrightBackend:
    """실제 Playwright 백엔드. 전용 스레드 안에서만 생성·사용된다."""

    def __init__(self, temp_dir: Path) -> None:
        from playwright.sync_api import sync_playwright

        self._temp_dir = temp_dir
        # Playwright 드라이버(Node)는 시작 시점의 TMP에 크로미움 임시 프로필을 만든다.
        # 드라이버를 띄우는 동안만 TMP를 앱 전용 폴더로 바꿔, 프로필이 그 안에 생기게 한다.
        saved = {key: os.environ.get(key) for key in _TEMP_ENV_KEYS}
        os.environ.update(dict.fromkeys(_TEMP_ENV_KEYS, str(temp_dir)))
        try:
            self._playwright = sync_playwright().start()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self._browsers: dict[bool, Any] = {}

    def new_context(self, headless: bool) -> RawContext:
        browser = self._browsers.get(headless)
        if browser is None:
            # Playwright의 env는 환경 전체를 바꾸므로 기존 환경 변수 위에 덮어쓴다.
            env: dict[str, str | float | bool] = {
                **os.environ,
                **dict.fromkeys(_TEMP_ENV_KEYS, str(self._temp_dir)),
            }
            browser = self._playwright.chromium.launch(headless=headless, env=env)
            self._browsers[headless] = browser
        context: RawContext = browser.new_context(accept_downloads=False)
        return context

    def close(self) -> None:
        for browser in self._browsers.values():
            browser.close()
        self._browsers.clear()
        self._playwright.stop()


class EphemeralContext:
    """비저장 브라우저 컨텍스트 하나. 세션 보관소에 `NativeSession`으로 넣을 수 있다."""

    def __init__(self, runtime: BrowserRuntime, raw: RawContext) -> None:
        self._runtime = runtime
        self._raw: RawContext | None = raw

    @property
    def closed(self) -> bool:
        return self._raw is None

    def call(self, func: Callable[[Any], T], timeout: float | None = None) -> T:
        """브라우저 스레드에서 func(원본 컨텍스트)를 실행한다."""
        raw = self._raw
        if raw is None:
            raise RuntimeError("브라우저 컨텍스트가 닫혔습니다")
        return self._runtime.run(lambda: func(raw), timeout)

    def close(self) -> None:
        raw = self._raw
        if raw is None:
            return
        # 닫기에 실패(예: 브라우저 스레드가 로그인 대기로 바쁨)하면 원본을 그대로 두어
        # 앱 종료(shutdown) 때 다시 닫는다. 실패를 '닫힘'으로 표시하지 않는다(보안 검토 M2).
        self._runtime.run(raw.close)
        self._raw = None
        self._runtime.forget(self)


class BrowserRuntime:
    def __init__(
        self,
        backend_factory: Callable[[Path], BrowserBackend] = PlaywrightBackend,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._factory = backend_factory
        self._timeout = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="smartmail-browser")
        self._lock = threading.Lock()
        self._backend: BrowserBackend | None = None
        self._temp_dir: Path | None = None
        self._contexts: set[EphemeralContext] = set()
        self._siblings: list[BrowserRuntime] = []

    def sibling(self) -> BrowserRuntime:
        """별도 스레드·별도 브라우저로 도는 실행기(결정기록 0026).

        오래 기다리는 작업(그룹웨어 인증문자 입력 대기 최대 180초)이 이 실행기의 유일한 스레드를
        잡고 있으면 AI-Ground 요청이 줄을 서다 시간 초과된다. 그런 작업은 형제 실행기에서 돌린다.
        종료·임시 폴더 삭제는 이 실행기의 shutdown()이 함께 처리한다.
        """
        child = BrowserRuntime(self._factory, self._timeout)
        with self._lock:
            self._siblings.append(child)
        return child

    @property
    def temp_dir(self) -> Path | None:
        return self._temp_dir

    def run(self, func: Callable[[], T], timeout: float | None = None) -> T:
        future = self._executor.submit(func)
        try:
            return future.result(timeout or self._timeout)
        except TimeoutError:
            # 아직 시작하지 않은 작업은 취소한다(시간 초과로 끝난 요청이 나중에 실행되지 않게)
            future.cancel()
            raise

    def _ensure_backend(self) -> BrowserBackend:
        if self._backend is None:
            self._temp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
            self._backend = self._factory(self._temp_dir)
        return self._backend

    def open_context(self, headless: bool = True) -> EphemeralContext:
        raw = self.run(lambda: self._ensure_backend().new_context(headless))
        context = EphemeralContext(self, raw)
        with self._lock:
            self._contexts.add(context)
        return context

    def forget(self, context: EphemeralContext) -> None:
        with self._lock:
            self._contexts.discard(context)

    def shutdown(self) -> None:
        """모든 컨텍스트·브라우저를 닫고 임시 폴더(쿠키·캐시 포함)를 지운다(형제 실행기 포함)."""
        with self._lock:
            contexts = list(self._contexts)
            siblings, self._siblings = self._siblings, []
        for child in siblings:
            child.shutdown()
        for context in contexts:
            try:
                context.close()
            except Exception:
                logger.warning("브라우저 컨텍스트 종료 실패")
        backend, self._backend = self._backend, None
        if backend is not None:
            try:
                self.run(backend.close)
            except Exception:
                logger.warning("브라우저 종료 실패")
        temp_dir, self._temp_dir = self._temp_dir, None
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if temp_dir.exists():
                logger.warning(
                    "브라우저 임시 폴더 일부를 지우지 못했습니다(다음 잔존 검사에서 재시도)"
                )
        self._executor.shutdown(wait=True)


class BrowserLoginAuthenticator:
    """사이트 로그인 화면을 띄워 사용자가 직접 로그인하게 한다(AUTH-09).

    로그인 완료 판정: 같은 호스트로 돌아오고 주소에 'login'이 없을 때.
    사이트별 실제 로그인 흐름(SSO 주소 등)은 확인 필요다.
    """

    def __init__(
        self,
        runtime: BrowserRuntime,
        start_url: str,
        wait_seconds: float = DEFAULT_LOGIN_WAIT_SECONDS,
    ) -> None:
        self._runtime = runtime
        self._start_url = start_url
        self._host = urlsplit(start_url).hostname
        self._wait_seconds = wait_seconds
        self._wait_ms = wait_seconds * 1000

    def _logged_in(self, url: str) -> bool:
        parts = urlsplit(url)
        return parts.hostname == self._host and "login" not in url.lower()

    def authenticate_interactive(self) -> NativeSession:
        context = self._runtime.open_context(headless=False)

        def drive(raw: Any) -> None:
            page = raw.new_page()
            page.goto(self._start_url)
            page.wait_for_url(self._logged_in, timeout=self._wait_ms)

        try:
            context.call(drive, timeout=self._wait_seconds + 30)
        except Exception:
            context.close()
            raise LoginFailedError("로그인을 완료하지 못했습니다") from None
        return context
