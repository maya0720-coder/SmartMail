"""자격증명 처리(AUTH-01~03, AUTH-08, AUTH-09).

- ID·암호는 로그인 요청 1회 동안만 `bytearray`로 다루고, 어댑터 호출이 끝나면 0으로 덮어쓴다.
- 반환값은 `SessionHandle`뿐이다. ID·암호를 담는 필드·속성·로그·예외 메시지는 없다.
- 확인된 사이트(AI-Ground·G-Knowledge·위키)는 입력값으로 사이트 로그인 화면을 자동으로 채운다
  (`security.site_login`, AUTH-09 개정, 결정기록 0011). 자동 로그인이 없는 사이트는
  `BrowserLoginAuthenticator`로 사이트 로그인 화면을 직접 띄우며, 이때 앱은 입력값을 받지 않는다.

한계: HTTP 요청 본문을 파싱하면 Python `str`(불변)이 잠시 생긴다. 이 값은 덮어쓸 수 없으므로
참조를 바로 끊어 GC에 맡긴다(결정기록 0005).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from security.logging_policy import EventType, Site, log_event
from security.sessions import NativeSession, SessionHandle, SessionStore

logger = logging.getLogger(__name__)

MAX_USER_ID_BYTES: Final = 100
MAX_SECRET_BYTES: Final = 256

# 로그인 창을 제공하는 사이트(AUTH-01). 아웃룩은 PC 클라이언트를 쓰므로 로그인이 없다.
LOGIN_SITES: Final = (Site.WIKI, Site.GKNOWLEDGE, Site.AIGROUND, Site.GROUPWARE)


class LoginFailedError(Exception):
    """로그인 실패. 메시지는 일반 문구만 쓴다."""


class CredentialsRequiredError(LoginFailedError):
    """SSO만으로 로그인되지 않아 ID·암호가 필요함."""


class LoginUnavailableError(Exception):
    """연동 규격이 확인되지 않아 로그인할 수 없음(확인 필요)."""


class InvalidLoginInputError(ValueError):
    """빈 값·너무 긴 값 등 입력 형식 오류."""


class PasswordAuthenticator(Protocol):
    """ID·암호로 로그인하는 사이트 어댑터. 인자를 저장하거나 기록하면 안 된다."""

    def authenticate(self, user_id: bytearray, secret: bytearray) -> NativeSession: ...


class InteractiveAuthenticator(Protocol):
    """사이트 로그인 화면을 띄워 사용자가 직접 입력하는 어댑터(AUTH-09)."""

    def authenticate_interactive(self) -> NativeSession: ...


def wipe(buffer: bytearray) -> None:
    """버퍼 내용을 0으로 덮어쓴다."""
    for index in range(len(buffer)):
        buffer[index] = 0


def _validate(user_id: bytearray, secret: bytearray) -> None:
    if not user_id or not secret:
        raise InvalidLoginInputError("ID와 암호를 입력해 주세요")
    if len(user_id) > MAX_USER_ID_BYTES or len(secret) > MAX_SECRET_BYTES:
        raise InvalidLoginInputError("입력 길이가 허용 범위를 넘었습니다")
    if any(byte < 0x20 for byte in user_id):
        raise InvalidLoginInputError("ID에 사용할 수 없는 문자가 있습니다")


@dataclass(frozen=True, slots=True)
class LoginService:
    """사이트별 어댑터로 로그인하고 세션 핸들만 돌려준다."""

    sessions: SessionStore
    password_authenticators: Mapping[Site, PasswordAuthenticator]
    interactive_authenticators: Mapping[Site, InteractiveAuthenticator]

    def uses_browser_login(self, site: Site) -> bool:
        """ID·암호 자동 로그인 어댑터가 없을 때만 사이트 화면 직접 로그인을 쓴다."""
        return site in self.interactive_authenticators and site not in self.password_authenticators

    def supports_password(self, site: Site) -> bool:
        return site in self.password_authenticators

    def try_sso(self, site: Site) -> SessionHandle | None:
        """입력 없이 SSO로만 로그인해 본다. 입력이 필요하거나 실패하면 None(실패 기록 없음)."""
        authenticator = self.password_authenticators.get(site)
        if authenticator is None or not getattr(authenticator, "supports_sso", False):
            return None
        try:
            native = authenticator.authenticate(bytearray(), bytearray())
        except CredentialsRequiredError:
            return None  # 입력이 필요할 뿐 실패가 아님
        except Exception:
            # 브라우저 오류·시간 초과는 조사할 수 있게 실패로 기록한다(보안 검토 L4)
            log_event(EventType.LOGIN, site, success=False)
            return None
        log_event(EventType.LOGIN, site, success=True)
        return self.sessions.open(site, native)

    def login(self, site: Site, user_id: bytearray, secret: bytearray) -> SessionHandle:
        """ID·암호 로그인. 성공·실패와 관계없이 끝나면 두 버퍼를 0으로 덮어쓴다."""
        try:
            if site not in LOGIN_SITES:
                raise InvalidLoginInputError("로그인 대상 사이트가 아닙니다")
            _validate(user_id, secret)
            authenticator = self.password_authenticators.get(site)
            if authenticator is None:
                raise LoginUnavailableError("이 사이트의 로그인 연동 규격이 확인되지 않았습니다")
            try:
                native = authenticator.authenticate(user_id, secret)
            except (LoginFailedError, LoginUnavailableError):
                raise
            except Exception:
                # 어댑터 예외 메시지에 입력값이 섞일 수 있으므로 원인을 연결하지 않는다.
                raise LoginFailedError("로그인하지 못했습니다") from None
        except Exception:
            log_event(EventType.LOGIN, site, success=False)
            raise
        finally:
            wipe(user_id)
            wipe(secret)
        log_event(EventType.LOGIN, site, success=True)
        return self.sessions.open(site, native)

    def login_interactive(self, site: Site) -> SessionHandle:
        """사이트 로그인 화면을 브라우저 창으로 띄운다(AUTH-09). 앱은 입력값을 받지 않는다."""
        authenticator = self.interactive_authenticators.get(site)
        if authenticator is None:
            raise LoginUnavailableError("이 사이트는 브라우저 로그인 대상으로 설정되지 않았습니다")
        try:
            native = authenticator.authenticate_interactive()
        except Exception:
            log_event(EventType.LOGIN, site, success=False)
            raise LoginFailedError("로그인하지 못했습니다") from None
        log_event(EventType.LOGIN, site, success=True)
        return self.sessions.open(site, native)


class _NullSession:
    def close(self) -> None:
        return None


class FakePasswordAuthenticator:
    """테스트·데모용 가짜 사이트. 실제 사이트에 접속하지 않는다.

    `accepted`가 주어지면 그 ID·암호 쌍만 통과시킨다. 비교용으로 메모리에 HMAC 다이제스트만 둔다.
    `accepted`가 없으면(데모 모드) 빈 값이 아닌 모든 입력을 통과시킨다.
    """

    def __init__(self, accepted: tuple[bytes, bytes] | None = None) -> None:
        self._key = secrets.token_bytes(32)
        self._expected = None if accepted is None else self._digest(*accepted)
        self.calls = 0
        self.last_session: _NullSession | None = None

    def _digest(self, user_id: bytes | bytearray, secret: bytes | bytearray) -> bytes:
        mac = hmac.new(self._key, digestmod=hashlib.sha256)
        mac.update(user_id)
        mac.update(b"\x00")
        mac.update(secret)
        return mac.digest()

    def authenticate(self, user_id: bytearray, secret: bytearray) -> NativeSession:
        self.calls += 1
        if self._expected is not None and not hmac.compare_digest(
            self._digest(user_id, secret), self._expected
        ):
            raise LoginFailedError("로그인하지 못했습니다")
        self.last_session = _NullSession()
        return self.last_session
