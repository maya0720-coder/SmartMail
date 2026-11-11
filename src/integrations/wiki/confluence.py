"""Confluence 위키 어댑터(WIKI-03·04·05).

REST API(페이지 조회·생성·수정, 버전 번호 충돌 검사)를 쓸 예정이다.
그러나 사내 위키의 API 허용 여부와
인증 방식은 아직 확인되지 않았다. 그래서 추측해서 호출하지 않고, 로그인 여부만 확인한 뒤
`WikiUnavailableError`로 안내한다. 로그인은 04단계 로그인 창(Site.WIKI)을 쓴다.
"""

from __future__ import annotations

from domain.weekly.service import WikiPage, WikiUnavailableError
from security.logging_policy import Site
from security.sessions import SessionStore

NOT_READY = "위키 API 허용·인증 방식이 확인되지 않아 아직 게시할 수 없습니다(확인 필요)"
LOGIN_REQUIRED = "위키 로그인이 필요합니다"


class ConfluenceWiki:
    def __init__(self, base_url: str, sessions: SessionStore) -> None:
        self._base_url = base_url
        self._sessions = sessions

    def _require_login(self) -> None:
        if self._sessions.current(Site.WIKI) is None:
            raise WikiUnavailableError(LOGIN_REQUIRED)

    def get_page(self, space_key: str, title: str) -> WikiPage | None:
        self._require_login()
        raise WikiUnavailableError(NOT_READY)

    def create_page(self, space_key: str, title: str, body: str) -> WikiPage:
        self._require_login()
        raise WikiUnavailableError(NOT_READY)

    def update_page(self, page: WikiPage, body: str) -> WikiPage:
        self._require_login()
        raise WikiUnavailableError(NOT_READY)
