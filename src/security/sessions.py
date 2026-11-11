"""사이트 세션 보관소(AUTH-05).

세션은 메모리에만 둔다. 로그아웃·유휴 시간 초과·앱 종료 시 폐기한다.
다른 모듈은 `SessionHandle`(사이트, 무작위 핸들 ID, 만료 시각)만 받는다. 핸들에는 ID·암호가 없다.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from domain.ports import Clock
from security.logging_policy import EventType, Site, log_event

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
    expires_at: datetime | None


class SessionStore:
    """사이트별 세션 1개를 메모리에 보관한다."""

    def __init__(self, clock: Clock, idle_timeout: timedelta) -> None:
        if idle_timeout <= timedelta(0):
            raise ValueError("유휴 시간은 0보다 커야 합니다")
        self._clock = clock
        self._idle = idle_timeout
        self._entries: dict[Site, _Entry] = {}
        self._lock = threading.RLock()

    def open(self, site: Site, native: NativeSession) -> SessionHandle:
        handle = SessionHandle(site, secrets.token_urlsafe(16))
        with self._lock:
            previous = self._entries.pop(site, None)
            self._entries[site] = _Entry(handle, native, self._clock.now())
        if previous is not None:
            _close_quietly(previous)
        return handle

    def _expire_if_idle(self, site: Site) -> _Entry | None:
        entry = self._entries.get(site)
        if entry is None:
            return None
        if self._clock.now() - entry.last_used >= self._idle:
            del self._entries[site]
            _close_quietly(entry)
            log_event(EventType.IDLE_TIMEOUT, site, success=True)
            return None
        return entry

    def use(self, handle: SessionHandle) -> NativeSession:
        """핸들로 실제 세션을 꺼낸다. 만료·로그아웃·교체된 핸들은 거부한다."""
        with self._lock:
            entry = self._expire_if_idle(handle.site)
            if entry is None or not secrets.compare_digest(
                entry.handle.handle_id, handle.handle_id
            ):
                raise SessionExpiredError("다시 로그인해 주세요")
            entry.last_used = self._clock.now()
            return entry.native

    def current(self, site: Site) -> SessionHandle | None:
        with self._lock:
            entry = self._expire_if_idle(site)
            return None if entry is None else entry.handle

    def status(self) -> list[SessionStatus]:
        result = []
        with self._lock:
            for site in Site:
                if site in (Site.APP, Site.OUTLOOK):
                    continue
                entry = self._expire_if_idle(site)
                expires = None if entry is None else entry.last_used + self._idle
                result.append(SessionStatus(site, entry is not None, expires))
        return result

    def sweep(self) -> None:
        """유휴 시간이 지난 세션을 호출이 없어도 닫는다(주기 작업에서 실행)."""
        with self._lock:
            for site in list(self._entries):
                self._expire_if_idle(site)

    def logout(self, site: Site) -> bool:
        with self._lock:
            entry = self._entries.pop(site, None)
        if entry is None:
            return False
        _close_quietly(entry)
        log_event(EventType.LOGOUT, site, success=True)
        return True

    def logout_all(self) -> int:
        """일괄 로그아웃(앱 종료 시에도 호출)."""
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            _close_quietly(entry)
            log_event(EventType.LOGOUT, entry.handle.site, success=True)
        return len(entries)


def _close_quietly(entry: _Entry) -> None:
    try:
        entry.native.close()
    except Exception:
        logger.warning("사이트 연결 종료 중 오류: site=%s", entry.handle.site.value)
