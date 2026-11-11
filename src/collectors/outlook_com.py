"""클래식 아웃룩 COM 수집기(MAIL-01, MAIL-03, MAIL-06, MAIL-08~10).

- pywin32로 로컬 아웃룩의 받은편지함·보낸편지함을 읽는다. 첨부는 열지 않고 파일명만 읽는다.
- 본문은 메모리에서만 쓴다. 저장은 참조 ID(EntryID, Internet Message-ID)뿐이다.
- 실제 아웃룩 연동은 자동 테스트에서 하지 않는다(수동 점검: docs/수동점검.md).
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from collectors.base import MAX_BODY_CHARS, SingleThreadRunner
from domain.mail import (
    CollectorUnavailableError,
    MailKey,
    MailMessage,
    MailThread,
    group_threads,
)
from domain.models import MailSource

logger = logging.getLogger(__name__)

OL_FOLDER_INBOX: Final = 6
OL_FOLDER_SENT: Final = 5
OL_MAIL_ITEM_CLASS: Final = 43
OL_CC: Final = 2
PR_INTERNET_MESSAGE_ID: Final = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
PR_TRANSPORT_HEADERS: Final = "http://schemas.microsoft.com/mapi/proptag/0x007D001F"
PR_SMTP_ADDRESS: Final = "http://schemas.microsoft.com/mapi/proptag/0x39FE001F"
MAX_ITEMS_PER_FOLDER: Final = 5000

AUTO_HEADER_MARKERS: Final = (
    "auto-submitted: auto",
    "precedence: bulk",
    "precedence: list",
    "precedence: junk",
    "list-unsubscribe:",
)


def _com_initializer() -> None:
    pythoncom = importlib.import_module("pythoncom")
    pythoncom.CoInitialize()


def default_outlook_factory() -> Any:
    win32_client = importlib.import_module("win32com.client")
    return win32_client.Dispatch("Outlook.Application")


def _local_time(value: Any) -> datetime:
    """아웃룩 시각(PC 현지 시각 벽시계 값)을 시간대 있는 값으로 바꾼다."""
    local_tz = datetime.now().astimezone().tzinfo
    naive = datetime(  # noqa: DTZ001 - 아래에서 시간대를 붙인다
        value.year, value.month, value.day, value.hour, value.minute, value.second
    )
    return naive.replace(tzinfo=local_tz)


def _dasl_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _safe(getter: Callable[[], Any], default: Any = "") -> Any:
    try:
        return getter()
    except Exception:
        return default


def _attr(target: Any, method: str, arg: Any, field: str | None = None) -> Any:
    """target.method(arg)[.field]를 호출하고 실패하면 빈 문자열을 돌려준다."""
    try:
        value = getattr(target, method)(arg)
        return getattr(value, field) if field else value
    except Exception:
        return ""


class OutlookComCollector:
    source = MailSource.OUTLOOK
    can_open_in_client = True

    def __init__(self, outlook_factory: Callable[[], Any] = default_outlook_factory) -> None:
        self._factory = outlook_factory
        self._runner = SingleThreadRunner("smartmail-outlook", _com_initializer)
        self._namespace: Any = None
        self._my_addresses: set[str] = set()

    def _ns(self) -> Any:
        if self._namespace is None:
            try:
                application = self._factory()
                namespace = application.GetNamespace("MAPI")
            except Exception:
                raise CollectorUnavailableError("클래식 아웃룩에 연결할 수 없습니다") from None
            self._namespace = namespace
            self._my_addresses = self._current_addresses(namespace)
        return self._namespace

    @staticmethod
    def _current_addresses(namespace: Any) -> set[str]:
        addresses = set()
        entry = _safe(lambda: namespace.CurrentUser.AddressEntry, None)
        if entry is not None:
            addresses.add(str(_safe(lambda: entry.Address)).casefold())
            exchange = _safe(lambda: entry.GetExchangeUser(), None)
            if exchange is not None:
                addresses.add(str(_safe(lambda: exchange.PrimarySmtpAddress)).casefold())
        return {a for a in addresses if a}

    def _recipient_flags(self, item: Any) -> tuple[bool, bool]:
        to_me = cc_me = False
        recipients = _safe(lambda: item.Recipients, None)
        count = int(_safe(lambda: recipients.Count, 0)) if recipients is not None else 0
        for index in range(1, count + 1):
            recipient: Any = recipients.Item(index)
            address = str(
                _attr(recipient.PropertyAccessor, "GetProperty", PR_SMTP_ADDRESS)
                or getattr(recipient, "Address", "")
            ).casefold()
            if address in self._my_addresses:
                if getattr(recipient, "Type", 1) == OL_CC:
                    cc_me = True
                else:
                    to_me = True
        return to_me, cc_me

    def _convert(self, item: Any, folder: str, sent_by_me: bool) -> MailMessage | None:
        if _safe(lambda: item.Class, None) != OL_MAIL_ITEM_CLASS:
            return None
        accessor = _safe(lambda: item.PropertyAccessor, None)
        message_id = str(_safe(lambda: accessor.GetProperty(PR_INTERNET_MESSAGE_ID)) or "")
        headers = str(_safe(lambda: accessor.GetProperty(PR_TRANSPORT_HEADERS)) or "").casefold()
        attachments = _safe(lambda: item.Attachments, None)
        count = int(_safe(lambda: attachments.Count, 0)) if attachments is not None else 0
        names = tuple(
            str(_attr(attachments, "Item", i, field="FileName")) for i in range(1, count + 1)
        )
        when = _safe(lambda: item.SentOn if sent_by_me else item.ReceivedTime, None)
        if when is None:
            return None
        to_me, cc_me = (False, False) if sent_by_me else self._recipient_flags(item)
        entry_id = str(item.EntryID)
        return MailMessage(
            key=MailKey(MailSource.OUTLOOK, entry_id, message_id or entry_id),
            thread_id=str(_safe(lambda: item.ConversationID) or entry_id),
            subject=str(_safe(lambda: item.Subject))[:500],
            sender_name=str(_safe(lambda: item.SenderName))[:200],
            sender_address=str(_safe(lambda: item.SenderEmailAddress))[:320],
            received_at=_local_time(when),
            folder=folder,
            sent_by_me=sent_by_me,
            to_me=to_me,
            cc_me=cc_me,
            is_auto=any(marker in headers for marker in AUTO_HEADER_MARKERS),
            body=str(_safe(lambda: item.Body))[:MAX_BODY_CHARS],
            attachment_names=names,
        )

    def _read_folder(
        self, folder_id: int, since: datetime, until: datetime, sent: bool
    ) -> list[MailMessage]:
        folder = self._ns().GetDefaultFolder(folder_id)
        field = "urn:schemas:httpmail:date"
        field += "sent" if sent else "received"
        query = f"@SQL=\"{field}\" >= '{_dasl_time(since)}' AND \"{field}\" < '{_dasl_time(until)}'"
        items = folder.Items.Restrict(query)
        result: list[MailMessage] = []
        for index in range(1, min(int(items.Count), MAX_ITEMS_PER_FOLDER) + 1):
            message = self._convert(items.Item(index), str(folder.Name), sent)
            if message is not None:
                result.append(message)
        return result

    def list_threads(self, since: datetime, until: datetime) -> list[MailThread]:
        def work() -> list[MailThread]:
            messages = self._read_folder(OL_FOLDER_INBOX, since, until, sent=False)
            messages += self._read_folder(OL_FOLDER_SENT, since, until, sent=True)
            return group_threads(messages)

        try:
            return self._runner.run(work)
        except CollectorUnavailableError:
            raise
        except Exception:
            logger.warning("아웃룩 메일 읽기 실패")
            raise CollectorUnavailableError("아웃룩 메일을 읽지 못했습니다") from None

    def get_message(self, key: MailKey) -> MailMessage | None:
        def work() -> MailMessage | None:
            try:
                item = self._ns().GetItemFromID(key.entry_id)
            except CollectorUnavailableError:
                raise
            except Exception:
                return None  # 삭제·이동(MAIL-09)
            folder = str(_safe(lambda: item.Parent.Name))
            sent = folder == str(_safe(lambda: self._ns().GetDefaultFolder(OL_FOLDER_SENT).Name))
            return self._convert(item, folder, sent)

        return self._runner.run(work)

    def open_in_client(self, key: MailKey) -> bool:
        def work() -> bool:
            try:
                self._ns().GetItemFromID(key.entry_id).Display()
            except Exception:
                return False
            return True

        return self._runner.run(work)

    def probe(self) -> None:
        """연결 확인. 실패하면 CollectorUnavailableError(MAIL-07)."""
        self._runner.run(lambda: self._ns().GetDefaultFolder(OL_FOLDER_INBOX))

    def close(self) -> None:
        self._runner.shutdown()
