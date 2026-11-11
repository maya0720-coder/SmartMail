"""세션 보관소·자격증명 처리 테스트(AUTH-02, AUTH-05, AUTH-09)."""

from __future__ import annotations

import secrets
from datetime import timedelta

import pytest

from domain.clock import FixedClock
from security.credentials import (
    MAX_SECRET_BYTES,
    FakePasswordAuthenticator,
    InvalidLoginInputError,
    LoginFailedError,
    LoginService,
    LoginUnavailableError,
    wipe,
)
from security.logging_policy import Site
from security.sessions import SessionExpiredError, SessionStore


class Closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _creds() -> tuple[bytes, bytes]:
    return secrets.token_hex(6).encode(), secrets.token_hex(12).encode()


def _service(clock: FixedClock, accepted: tuple[bytes, bytes] | None = None) -> LoginService:
    store = SessionStore(clock, timedelta(minutes=30))
    fake = FakePasswordAuthenticator(accepted)
    return LoginService(store, {Site.WIKI: fake}, {})


def test_auth02_buffers_wiped_after_success(clock: FixedClock) -> None:
    user, pw = _creds()
    service = _service(clock, (user, pw))
    id_buf, pw_buf = bytearray(user), bytearray(pw)
    handle = service.login(Site.WIKI, id_buf, pw_buf)
    assert set(id_buf) == {0} and set(pw_buf) == {0}
    assert handle.site is Site.WIKI
    assert user.decode() not in repr(handle) and pw.decode() not in repr(handle)


def test_auth02_buffers_wiped_after_failure(clock: FixedClock) -> None:
    user, pw = _creds()
    service = _service(clock, (user, pw))
    wrong = bytearray(secrets.token_hex(12).encode())
    id_buf = bytearray(user)
    with pytest.raises(LoginFailedError) as info:
        service.login(Site.WIKI, id_buf, wrong)
    assert set(id_buf) == {0} and set(wrong) == {0}
    assert user.decode() not in str(info.value)


@pytest.mark.parametrize(
    ("user", "pw"),
    [
        (b"", b"x"),
        (b"x", b""),
        (b"x" * 101, b"y"),
        (b"x", b"y" * (MAX_SECRET_BYTES + 1)),
        (b"a\nb", b"y"),
    ],
)
def test_invalid_input_rejected_and_wiped(clock: FixedClock, user: bytes, pw: bytes) -> None:
    service = _service(clock)
    id_buf, pw_buf = bytearray(user), bytearray(pw)
    with pytest.raises(InvalidLoginInputError):
        service.login(Site.WIKI, id_buf, pw_buf)
    assert not any(id_buf) and not any(pw_buf)


def test_site_without_adapter_is_unavailable(clock: FixedClock) -> None:
    with pytest.raises(LoginUnavailableError):
        _service(clock).login(Site.GKNOWLEDGE, bytearray(b"a"), bytearray(b"b"))


def test_adapter_exception_message_not_propagated(clock: FixedClock) -> None:
    marker = secrets.token_hex(8)

    class Leaky:
        def authenticate(self, user_id: bytearray, secret: bytearray) -> Closable:
            raise RuntimeError("bad " + marker)

    service = LoginService(SessionStore(clock, timedelta(minutes=5)), {Site.WIKI: Leaky()}, {})
    with pytest.raises(LoginFailedError) as info:
        service.login(Site.WIKI, bytearray(b"a"), bytearray(b"b"))
    assert marker not in str(info.value)
    assert info.value.__cause__ is None


def test_auth05_idle_timeout_expires_session(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30))
    native = Closable()
    handle = store.open(Site.WIKI, native)
    clock.advance(timedelta(minutes=29))
    assert store.use(handle) is native
    clock.advance(timedelta(minutes=30))
    with pytest.raises(SessionExpiredError):
        store.use(handle)
    assert native.closed


def test_auth05_logout_and_logout_all(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30))
    wiki, gk = Closable(), Closable()
    h1 = store.open(Site.WIKI, wiki)
    h2 = store.open(Site.GKNOWLEDGE, gk)
    assert store.logout(Site.WIKI)
    with pytest.raises(SessionExpiredError):
        store.use(h1)
    assert store.logout_all() == 1
    with pytest.raises(SessionExpiredError):
        store.use(h2)
    assert wiki.closed and gk.closed


def test_replaced_handle_rejected(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30))
    old = store.open(Site.WIKI, Closable())
    store.open(Site.WIKI, Closable())
    with pytest.raises(SessionExpiredError):
        store.use(old)


def test_status_lists_login_sites_only(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30))
    store.open(Site.WIKI, Closable())
    status = {s.site: s.logged_in for s in store.status()}
    assert status == {
        Site.WIKI: True,
        Site.GKNOWLEDGE: False,
        Site.AIGROUND: False,
        Site.GROUPWARE: False,
    }


def test_auth09_interactive_login_receives_no_values(clock: FixedClock) -> None:
    class Interactive:
        def authenticate_interactive(self) -> Closable:
            return Closable()

    store = SessionStore(clock, timedelta(minutes=30))
    service = LoginService(store, {}, {Site.GKNOWLEDGE: Interactive()})
    assert service.uses_browser_login(Site.GKNOWLEDGE)
    handle = service.login_interactive(Site.GKNOWLEDGE)
    assert store.current(Site.GKNOWLEDGE) == handle


def test_wipe_zeroes_buffer() -> None:
    buf = bytearray(b"abc")
    wipe(buf)
    assert buf == bytearray(3)
