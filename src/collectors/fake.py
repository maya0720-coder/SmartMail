"""테스트·데모용 가짜 수집기. 실제 메일함에 접근하지 않는다."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from domain.mail import MailKey, MailMessage, MailThread, group_threads
from domain.models import MailSource


class FakeMailCollector:
    can_open_in_client = True

    def __init__(
        self, messages: Iterable[MailMessage] = (), source: MailSource = MailSource.OUTLOOK
    ) -> None:
        self.source = source
        self._messages = {m.key.entry_id: m for m in messages}
        self.opened: list[str] = []

    def add(self, message: MailMessage) -> None:
        self._messages[message.key.entry_id] = message

    def delete(self, entry_id: str) -> None:
        """원본 삭제·이동 상황 재현(MAIL-09)."""
        self._messages.pop(entry_id, None)

    def list_threads(self, since: datetime, until: datetime) -> list[MailThread]:
        selected = [m for m in self._messages.values() if since <= m.received_at < until]
        return group_threads(selected)

    def get_message(self, key: MailKey) -> MailMessage | None:
        return self._messages.get(key.entry_id)

    def open_in_client(self, key: MailKey) -> bool:
        if key.entry_id not in self._messages:
            return False
        self.opened.append(key.entry_id)
        return True
