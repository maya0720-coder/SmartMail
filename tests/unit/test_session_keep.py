"""세션 유지·연쇄 폐기 수정 테스트(AUTH-05, 결정기록 0031: C1·C2·C3·H4)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from domain.clock import FixedClock
from security.keepalive import SessionKeepAlive
from security.logging_policy import AUDIT_LOGGER_NAME, EndReason, Site
from security.sessions import NativeSession, SessionExpiredError, SessionStore
from security.site_login import SharedContext


class FakeContext:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _bundle(
    clock: FixedClock, *, keep: bool = False
) -> tuple[SessionStore, SharedContext, FakeContext]:
    raw = FakeContext()
    shared = SharedContext(raw)  # type: ignore[arg-type]
    store = SessionStore(clock, timedelta(minutes=30), keep_while_running=keep)
    return store, shared, raw


def test_auth05_c1_bundle_idle_clock_keeps_unused_company_site(clock: FixedClock) -> None:
    # AI-Ground만 계속 쓰고 G-Knowledge는 쓰지 않아도 묶음 시계라 둘 다 유지된다
    store, shared, raw = _bundle(clock)
    ai = store.open(Site.AIGROUND, shared.lease())
    store.open(Site.GKNOWLEDGE, shared.lease())
    for _ in range(4):
        clock.advance(timedelta(minutes=10))
        store.use(ai)
        store.sweep()
    assert store.current(Site.GKNOWLEDGE) is not None
    assert store.current(Site.AIGROUND) is not None
    assert not raw.closed


def test_auth05_c1_bundle_expires_together_when_unused(clock: FixedClock) -> None:
    store, shared, raw = _bundle(clock)
    store.open(Site.AIGROUND, shared.lease())
    store.open(Site.GKNOWLEDGE, shared.lease())
    clock.advance(timedelta(minutes=31))
    store.sweep()
    assert raw.closed
    assert store.ended_reason(Site.AIGROUND) is EndReason.IDLE_TIMEOUT


def test_auth05_c2_relogin_keeps_shared_context(clock: FixedClock) -> None:
    store, shared, raw = _bundle(clock)
    ai = store.open(Site.AIGROUND, shared.lease())
    store.open(Site.GKNOWLEDGE, shared.lease())
    gk_new = store.open(Site.GKNOWLEDGE, shared.lease())  # 재로그인(같은 공용 컨텍스트)
    assert not raw.closed
    assert store.use(ai) is not None
    assert store.use(gk_new) is not None
    assert store.current(Site.AIGROUND) is not None


def test_auth05_c2_relogin_with_plain_context_closes_previous(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30))
    old_native = FakeContext()
    old = store.open(Site.WIKI, old_native)
    store.open(Site.WIKI, FakeContext())
    assert old_native.closed
    with pytest.raises(SessionExpiredError):
        store.use(old)


def test_auth05_m1_explicit_logout_discards_bundle(clock: FixedClock) -> None:
    store, shared, raw = _bundle(clock, keep=True)
    store.open(Site.AIGROUND, shared.lease())
    store.open(Site.GKNOWLEDGE, shared.lease())
    assert store.logout(Site.GKNOWLEDGE)
    assert raw.closed
    assert store.current(Site.AIGROUND) is None
    assert store.ended_reason(Site.AIGROUND) is EndReason.CASCADE
    assert store.ended_reason(Site.GKNOWLEDGE) is EndReason.LOGOUT


def test_auth05_keep_while_running_survives_eight_hours(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30), keep_while_running=True)
    native = FakeContext()
    handle = store.open(Site.WIKI, native)
    clock.advance(timedelta(hours=8))
    store.sweep()
    assert store.use(handle) is native
    status = {s.site: s for s in store.status()}
    assert status[Site.WIKI].logged_in and status[Site.WIKI].expires_at is None


def test_auth05_keep_off_uses_idle_rule(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30), keep_while_running=False)
    native = FakeContext()
    handle = store.open(Site.WIKI, native)
    clock.advance(timedelta(minutes=31))
    with pytest.raises(SessionExpiredError):
        store.use(handle)
    assert native.closed
    status = {s.site: s for s in store.status()}
    assert status[Site.WIKI].ended_reason is EndReason.IDLE_TIMEOUT


def test_auth05_relogin_clears_ended_reason(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30))
    store.open(Site.WIKI, FakeContext())
    store.logout(Site.WIKI)
    assert store.ended_reason(Site.WIKI) is EndReason.LOGOUT
    store.open(Site.WIKI, FakeContext())
    assert store.ended_reason(Site.WIKI) is None


class StubProbe:
    def __init__(self, result: bool | None, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls = 0

    def check(self, native: NativeSession) -> bool | None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("probe broke")
        return self.result


def test_auth05_h4_keepalive_detects_site_expiry(clock: FixedClock) -> None:
    store, shared, raw = _bundle(clock, keep=True)
    store.open(Site.AIGROUND, shared.lease())
    store.open(Site.GKNOWLEDGE, shared.lease())
    keepalive = SessionKeepAlive(
        store, {Site.AIGROUND: StubProbe(False), Site.GKNOWLEDGE: StubProbe(True)}
    )
    assert keepalive.run_once() == [Site.AIGROUND]
    assert raw.closed
    assert store.ended_reason(Site.AIGROUND) is EndReason.SITE_EXPIRED
    assert store.ended_reason(Site.GKNOWLEDGE) is EndReason.CASCADE


def test_auth05_h4_keepalive_failure_or_unknown_keeps_session(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30), keep_while_running=True)
    store.open(Site.AIGROUND, FakeContext())
    store.open(Site.GKNOWLEDGE, FakeContext())
    keepalive = SessionKeepAlive(
        store, {Site.AIGROUND: StubProbe(None, fail=True), Site.GKNOWLEDGE: StubProbe(None)}
    )
    assert keepalive.run_once() == []
    assert store.current(Site.AIGROUND) is not None
    assert store.current(Site.GKNOWLEDGE) is not None


def test_auth05_h4_keepalive_skips_when_busy(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30), keep_while_running=True)
    store.open(Site.AIGROUND, FakeContext())
    probe = StubProbe(False)
    keepalive = SessionKeepAlive(store, {Site.AIGROUND: probe}, busy=lambda: True)
    assert keepalive.run_once() == []
    assert probe.calls == 0


def test_auth05_keepalive_does_not_refresh_idle_clock(clock: FixedClock) -> None:
    store = SessionStore(clock, timedelta(minutes=30))
    store.open(Site.AIGROUND, FakeContext())
    keepalive = SessionKeepAlive(store, {Site.AIGROUND: StubProbe(True)})
    clock.advance(timedelta(minutes=20))
    keepalive.run_once()
    clock.advance(timedelta(minutes=11))
    assert store.current(Site.AIGROUND) is None  # 점검은 사용으로 치지 않는다


def test_auth08_end_reason_logged_without_secrets(
    clock: FixedClock, caplog: pytest.LogCaptureFixture
) -> None:
    store = SessionStore(clock, timedelta(minutes=30))
    store.open(Site.WIKI, FakeContext())
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
        store.open(Site.WIKI, FakeContext())
        store.logout_all(EndReason.APP_STOP)
    text = caplog.text
    assert "event=disconnect site=wiki result=success reason=replaced" in text
    assert "reason=app_stop" in text
    assert "handle" not in text.lower()
