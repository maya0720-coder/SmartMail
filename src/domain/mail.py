"""메일 수집 인터페이스와 메모리 전용 메일 모델(MAIL-01~10).

`MailMessage.body`는 화면 표시·업무 추출 동안 메모리에서만 쓰고 저장하지 않는다(MAIL-08).
수집기 구현은 `collectors` 패키지가 제공한다(의존 방향 domain ← collectors).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from domain.models import MailSource


class CollectorUnavailableError(Exception):
    """수집 경로를 쓸 수 없음(아웃룩 COM 실패, 그룹웨어 연동 미확인 등)."""


@dataclass(frozen=True, slots=True)
class MailKey:
    """원본 메일을 다시 찾기 위한 참조(저장 대상은 이것뿐)."""

    source: MailSource
    entry_id: str
    message_id: str


@dataclass(frozen=True, slots=True)
class MailMessage:
    key: MailKey
    thread_id: str
    subject: str
    sender_name: str
    received_at: datetime
    folder: str
    sent_by_me: bool = False
    to_me: bool = False  # 받는 사람에 내가 있음
    cc_me: bool = False  # 참조에 내가 있음
    is_auto: bool = False  # 자동발송 헤더(Auto-Submitted, Precedence: bulk 등)
    sender_address: str = ""  # 제외 규칙 판정용(메모리 전용)
    body: str = field(default="", repr=False)  # 메모리 전용, 저장 금지
    attachment_names: tuple[str, ...] = ()  # 파일명만(MAIL-06)

    def __repr__(self) -> str:
        return f"MailMessage(thread_id={self.thread_id!r}, folder={self.folder!r})"


@dataclass(frozen=True, slots=True)
class MailThread:
    thread_id: str
    messages: tuple[MailMessage, ...]

    @property
    def first(self) -> MailMessage:
        return self.messages[0]

    @property
    def latest(self) -> MailMessage:
        return self.messages[-1]


class MailCollector(Protocol):
    source: MailSource
    can_open_in_client: bool

    def list_threads(self, since: datetime, until: datetime) -> list[MailThread]:
        """[since, until) 기간의 받은·보낸 메일을 스레드로 묶어 돌려준다(MAIL-03·04)."""
        ...

    def get_message(self, key: MailKey) -> MailMessage | None:
        """원본을 다시 읽는다. 삭제·이동돼 찾을 수 없으면 None(MAIL-09)."""
        ...

    def open_in_client(self, key: MailKey) -> bool:
        """메일 클라이언트에서 원본을 연다(MAIL-10). 지원하지 않으면 False."""
        ...


def group_threads(messages: list[MailMessage]) -> list[MailThread]:
    """스레드 ID로 묶고 스레드 안은 시각순, 스레드는 첫 메일 시각순으로 정렬한다."""
    grouped: dict[str, list[MailMessage]] = {}
    for message in messages:
        grouped.setdefault(message.thread_id, []).append(message)
    threads = [
        MailThread(tid, tuple(sorted(items, key=lambda m: m.received_at)))
        for tid, items in grouped.items()
    ]
    return sorted(threads, key=lambda t: t.first.received_at)
