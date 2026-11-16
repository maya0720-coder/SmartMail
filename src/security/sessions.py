"""사이트 세션 보관소(AUTH-05, 결정기록 0031).

세션은 메모리에만 둔다. 명시적 로그아웃·앱 종료·사이트 쪽 만료 시 폐기한다.
'실행 중 유지'를 끄면(keep_while_running=False) 유휴 시간 초과로도 폐기한다.
다른 모듈은 `SessionHandle`(사이트, 무작위 핸들 ID)만 받는다. 핸들에는 ID·암호가 없다.

회사 계정 묶음(공용 컨텍스트를 함께 쓰는 사이트)은 유휴 시계를 묶음 단위로 센다.
한 사이트를 쓰면 같은 묶음의 다른 사이트도 쓴 것으로 본다(C1 연쇄 폐기 방지).
같은 공용 컨텍스트로 다시 로그인하면 이전 임대는 참조만 끊는다(C2 자기 폐기 방지).
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from domain.ports import Clock
from security.logging_policy import EndReason, EventType, Site, log_event

logger = logging.getLogger(__name__)


class SessionExpiredError(Exception):
    """세션이 없거나 만료됨. 다시 로그인해야 한다."""


class NativeSession(Protocol):
    """사이트 어댑터가 만든 실제 세션(HTTP 클라이언트·브라우저 컨텍스트 등)."""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionHandle:
    site: Site
    handle_id: str

    def __repr__(self) -> str:
        return f"SessionHandle(site={self.site.value})"


@dataclass(slots=True)
class _Entry:
    handle: SessionHandle
    native: NativeSession
    last_used: datetime


@dataclass(frozen=True, slots=True)
class SessionStatus:
    site: Site
    logged_in: bool
    expires_at: datetime | None  # 실행 중 유지면 None(유휴 만료 없음)
    ended_reason: EndReason | None = None  # 로그인 안 됨일 때 마지막 종료 사유


def _group_of(native: NativeSession) -> object | None:
    """같은 공용 컨텍스트를 쓰는 세션끼리 같은 값을 돌려준다(없으면 None)."""
    return getattr(native, "group", None)


class SessionStore:
    """사이트별 세션 1개를 메모리에 보관한다."""

    def __init__(
        self, clock: Clock, idle_timeout: timedelta, *, keep_while_running: bool = False
    ) -> None:
        if idle_timeout <= timedelta(0):
            raise ValueError("유휴 시간은 0보다 커야 합니다")
        self._clock = clock
        self._idle = idle_timeout
        self._keep = keep_while_running
        self._entries: dict[Site, _Entry] = {}
        self._ended: dict[Site, EndReason] = {}
        self._lock = threading.RLock()

    @property
    def keep_while_running(self) -> bool:
        return self._keep

    def open(self, site: Site, native: NativeSession) -> SessionHandle:
        handle = SessionHandle(site, secrets.token_urlsafe(16))
        with self._lock:
            previous = self._entries.pop(site, None)
            entry = _Entry(handle, native, self._clock.now())
            self._entries[site] = entry
            self._ended.pop(site, None)
            self._touch_group(entry)
        if previous is not None:
            group = _group_of(native)
            detach = getattr(previous.native, "detach", None)
            if group is not None and group is _group_of(previous.native) and callable(detach):
                detach()  # 같은 공용 컨텍스트: 새 임대와 다른 사이트 세션을 살려 둔다(C2)
            else:
                _close_quietly(previous)
            _log_end(site, EndReason.REPLACED)
        return handle

    def _touch_group(self, entry: _Entry) -> None:
        """같은 묶음 세션의 마지막 사용 시각을 함께 갱신한다(잠금 안에서 호출)."""
        entry.last_used = self._clock.now()
        group = _group_of(entry.native)
        if group is None:
            return
        for other in self._entries.values():
            if other is not entry and _group_of(other.native) is group:
                other.last_used = entry.last_used

    def _expire_if_idle(self, site: Site, expired: list[tuple[_Entry, EndReason]]) -> _Entry | None:
        """만료됐거나 실제 세션이 이미 닫혔으면 목록에서 빼고 `expired`에 담는다.

        닫기(close)는 잠금 밖에서 한다. 브라우저 닫기가 오래 걸려도 다른 조회가 멈추지 않게 한다.
        """
        entry = self._entries.get(site)
        if entry is None:
            return None
        reason: EndReason | None = None
        if not self._keep and self._clock.now() - entry.last_used >= self._idle:
            reason = EndReason.IDLE_TIMEOUT
        elif getattr(entry.native, "closed", False):
            # 함께 쓰던 공용 컨텍스트가 다른 사이트 쪽에서 폐기된 경우
            reason = EndReason.CASCADE
        if reason is None:
            return entry
        del self._entries[site]
        self._ended[site] = reason
        expired.append((entry, reason))
        return None

    @staticmethod
    def _close_all(expired: list[tuple[_Entry, EndReason]]) -> None:
        for entry, reason in expired:
            _close_quietly(entry)
            if reason is EndReason.IDLE_TIMEOUT:
                log_event(EventType.IDLE_TIMEOUT, entry.handle.site, success=True)
            _log_end(entry.handle.site, reason)

    def use(self, handle: SessionHandle) -> NativeSession:
        """핸들로 실제 세션을 꺼낸다. 만료·로그아웃·교체된 핸들은 거부한다."""
        expired: list[tuple[_Entry, EndReason]] = []
        try:
            with self._lock:
                entry = self._expire_if_idle(handle.site, expired)
                if entry is None or not secrets.compare_digest(
                    entry.handle.handle_id, handle.handle_id
                ):
                    raise SessionExpiredError("다시 로그인해 주세요")
                self._touch_group(entry)
                return entry.native
        finally:
            self._close_all(expired)

    def current(self, site: Site) -> SessionHandle | None:
        expired: list[tuple[_Entry, EndReason]] = []
        with self._lock:
            entry = self._expire_if_idle(site, expired)
        self._close_all(expired)
        return None if entry is None else entry.handle

    def peek(self, site: Site) -> NativeSession | None:
        """연결 유지 점검용: 사용 시각을 바꾸지 않고 실제 세션을 꺼낸다."""
        expired: list[tuple[_Entry, EndReason]] = []
        with self._lock:
            entry = self._expire_if_idle(site, expired)
        self._close_all(expired)
        return None if entry is None else entry.native

    def ended_reason(self, site: Site) -> EndReason | None:
        with self._lock:
            return self._ended.get(site)

    def status(self) -> list[SessionStatus]:
        result = []
        expired: list[tuple[_Entry, EndReason]] = []
        with self._lock:
            for site in Site:
                if site in (Site.APP, Site.OUTLOOK):
                    continue
                entry = self._expire_if_idle(site, expired)
                expires = None
                if entry is not None and not self._keep:
                    expires = entry.last_used + self._idle
                ended = None if entry is not None else self._ended.get(site)
                result.append(SessionStatus(site, entry is not None, expires, ended))
        self._close_all(expired)
        return result

    def sweep(self) -> None:
        """유휴 시간이 지났거나 닫힌 세션을 호출이 없어도 정리한다(주기 작업에서 실행)."""
        expired: list[tuple[_Entry, EndReason]] = []
        with self._lock:
            for site in list(self._entries):
                self._expire_if_idle(site, expired)
        self._close_all(expired)

    def expire(self, site: Site, reason: EndReason = EndReason.SITE_EXPIRED) -> bool:
        """사이트 쪽 만료를 감지했을 때 세션을 정리한다. 공용 컨텍스트면 묶음 전체가 닫힌다."""
        with self._lock:
            entry = self._entries.pop(site, None)
            if entry is None:
                return False
            self._ended[site] = reason
        _close_quietly(entry)
        _log_end(site, reason)
        self.sweep()  # 함께 닫힌 묶음 세션을 바로 정리(CASCADE)
        return True

    def logout(self, site: Site) -> bool:
        with self._lock:
            entry = self._entries.pop(site, None)
            if entry is None:
                return False
            self._ended[site] = EndReason.LOGOUT
        _close_quietly(entry)
        log_event(EventType.LOGOUT, site, success=True)
        _log_end(site, EndReason.LOGOUT)
        return True

    def logout_all(self, reason: EndReason = EndReason.LOGOUT) -> int:
        """일괄 로그아웃(앱 종료 시에는 reason=APP_STOP으로 호출)."""
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
            for entry in entries:
                self._ended[entry.handle.site] = reason
        for entry in entries:
            _close_quietly(entry)
            log_event(EventType.LOGOUT, entry.handle.site, success=True)
            _log_end(entry.handle.site, reason)
        return len(entries)


def _log_end(site: Site, reason: EndReason) -> None:
    log_event(EventType.DISCONNECT, site, success=True, reason=reason)


def _close_quietly(entry: _Entry) -> None:
    try:
        entry.native.close()
    except Exception:
        logger.warning("사이트 연결 종료 중 오류: site=%s", entry.handle.site.value)
