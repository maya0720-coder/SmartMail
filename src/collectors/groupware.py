"""그룹웨어 메일함 수집기(MAIL-02).

그룹웨어 연동 방식(API/IMAP)은 확인 필요다. 추측해서 구현하지 않고, 로그인 여부만 확인한 뒤
`CollectorUnavailableError`를 던진다. 로그인은 04단계 로그인 창(Site.GROUPWARE)을 쓴다.
"""

from __future__ import annotations

from datetime import datetime

from domain.mail import CollectorUnavailableError, MailKey, MailMessage, MailThread
from domain.models import MailSource
from security.logging_policy import Site
from security.sessions import SessionStore

NOT_READY_MESSAGE = "그룹웨어 메일 연동 방식이 확인되지 않아 아직 수집할 수 없습니다(확인 필요)"


class GroupwareMailCollector:
    source = MailSource.GROUPWARE
    can_open_in_client = False

    def __init__(self, sessions: SessionStore) -> None:
        self._sessions = sessions

    def _require_login(self) -> None:
        if self._sessions.current(Site.GROUPWARE) is None:
            raise CollectorUnavailableError("그룹웨어 로그인이 필요합니다")

    def list_threads(self, since: datetime, until: datetime) -> list[MailThread]:
        self._require_login()
        raise CollectorUnavailableError(NOT_READY_MESSAGE)

    def get_message(self, key: MailKey) -> MailMessage | None:
        self._require_login()
        raise CollectorUnavailableError(NOT_READY_MESSAGE)

    def open_in_client(self, key: MailKey) -> bool:
        return False
