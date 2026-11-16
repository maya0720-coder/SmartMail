"""수집할 때 업무의 스레드 정보를 갱신한다(결정기록 0027·0028). 순수 로직.

- 마지막 메일(시각·방향): 업무에 연결된 모든 메일 참조 중 가장 늦은 것. 수집 창에 일부 메일만
  들어와도 저장된 참조 전체로 계산한다.
- 요청자 구분: 아직 없으면 스레드의 첫 받은 메일 발신 도메인으로 계산한다(주소는 저장하지 않음).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from domain.mail import MailThread
from domain.models import MAIL_IN, MAIL_OUT, MailRef, Task
from domain.tasks.requester import RequesterClassifier


def _direction(ref: MailRef, thread: MailThread) -> str | None:
    if ref.direction is not None:
        return ref.direction
    for message in thread.messages:
        if message.key.message_id == ref.message_id:
            return MAIL_OUT if message.sent_by_me else MAIL_IN
    return None


def sync_thread_fields(
    task: Task,
    thread: MailThread,
    refs: Sequence[MailRef],
    classifier: RequesterClassifier | None,
) -> Task:
    updated = task
    if refs:
        last = max(refs, key=lambda r: r.received_at)
        updated = replace(
            updated, last_mail_at=last.received_at, last_mail_direction=_direction(last, thread)
        )
    if classifier is not None and updated.requester_kind is None:
        received = [m for m in thread.messages if not m.sent_by_me]
        requester = classifier.classify(received[0].sender_address) if received else None
        if requester is not None:
            updated = replace(
                updated, requester_kind=requester.kind, requester_domain=requester.domain
            )
    return updated
