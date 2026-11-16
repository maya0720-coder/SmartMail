"""연결 유지 점검(keep-alive, 결정기록 0031 H4 대응).

로그인된 사이트마다 정해진 주기로 기존 브라우저 컨텍스트에서 사이트 첫 화면을 열어
로그인 표식(`login_fields.toml`의 `site_logged_in`)이 보이는지만 확인한다.

- 새 데이터는 보내지 않는다. 화면 글자·주소·쿠키는 기록하지 않는다.
- 다른 호스트(로그인 서버)로 바뀌었으면 사이트 쪽 세션이 끝난 것으로 보고 `SITE_EXPIRED`로 정리한다.
  같은 호스트인데 표식만 없으면 판단하지 않는다(화면 변경·느린 응답으로 오판하지 않게).
- 요약 작업이 실행 중이면 브라우저 대기열을 막지 않도록 이번 점검을 건너뛴다.
- 점검 중 예외는 기록만 하고 앱을 멈추지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from security.logging_policy import EndReason, Site
from security.sessions import NativeSession, SessionStore

logger = logging.getLogger(__name__)

PROBE_PAGE_TIMEOUT_MS: Final = 30_000
PROBE_MARKER_TIMEOUT_MS: Final = 10_000
PROBE_CALL_TIMEOUT_SECONDS: Final = 60.0


class SiteProbe(Protocol):
    """True=로그인 유지, False=로그인이 끝남(로그인 화면), None=판단할 수 없음."""

    def check(self, native: NativeSession) -> bool | None: ...


class BrowserMarkerProbe:
    """기존 브라우저 컨텍스트로 사이트 첫 화면을 열어 로그인 표식을 확인한다."""

    def __init__(self, url: str, marker: str) -> None:
        self._url = url
        self._host = (urlsplit(url).hostname or "").lower()
        self._marker = marker

    def check(self, native: NativeSession) -> bool | None:
        call = getattr(native, "call", None)
        if not callable(call):
            return None
        result = call(self._probe, PROBE_CALL_TIMEOUT_SECONDS)
        return result if isinstance(result, bool) else None

    def _probe(self, raw: Any) -> bool | None:
        page = raw.new_page()
        try:
            page.goto(self._url, timeout=PROBE_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
            try:
                page.locator(self._marker).first.wait_for(
                    state="visible", timeout=PROBE_MARKER_TIMEOUT_MS
                )
                return True
            except Exception:
                host = (urlsplit(str(page.url)).hostname or "").lower()
                return False if host and host != self._host else None
        finally:
            page.close()


class SessionKeepAlive:
    """로그인된 사이트를 주기적으로 점검하고, 사이트 쪽에서 끝난 세션을 정리한다."""

    def __init__(
        self,
        sessions: SessionStore,
        probes: Mapping[Site, SiteProbe],
        busy: Callable[[], bool] = lambda: False,
    ) -> None:
        self._sessions = sessions
        self._probes = dict(probes)
        self._busy = busy

    @property
    def enabled(self) -> bool:
        return bool(self._probes)

    def run_once(self) -> list[Site]:
        """점검 1회. 사이트 쪽 만료로 정리한 사이트 목록을 돌려준다."""
        ended: list[Site] = []
        for site, probe in self._probes.items():
            if self._busy():
                break  # 요약 작업과 겹치면 다음 주기로 미룬다
            native = self._sessions.peek(site)
            if native is None:
                continue
            try:
                alive = probe.check(native)
            except Exception:
                logger.warning("연결 유지 점검 실패: site=%s", site.value)
                continue
            if alive is False and self._sessions.expire(site, EndReason.SITE_EXPIRED):
                ended.append(site)
        return ended
