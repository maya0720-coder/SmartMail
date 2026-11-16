"""회신 대기·팔로업(U7, TODO-05·07, 결정기록 0038). 순수 로직.

- 판정: 업무 스레드의 **마지막 메일이 내가 보낸 메일**이고, 그 메일에 질문·요청 표현이 있으면
  '회신 대기'다. 질문·요청 표현은 수집 시점 메모리에서만 본다(본문·문장은 저장하지 않음).
  사용자가 직접 '회신 대기로 표시'할 수도 있다.
- 상대 회신(받은 메일)이 마지막이 되면 자동으로 풀린다. 사용자가 '대기 해제'를 누르면 같은 메일로는
  다시 켜지지 않는다(새로 보낸 질문 메일이 오면 다시 켜진다).
- 대기 시작 뒤 근무일(주말·공휴일 제외)이 설정값(기본 2) 이상 지나면 '팔로업 필요'다.
- 상태 체계(TODO-03)는 바꾸지 않는 보조 표시다. 메일 발송 기능은 없다(초안 문안 복사만).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Final

from domain.calendar import business_days_after
from domain.mail import MailThread
from domain.models import MAIL_IN, MAIL_OUT, MailRef, Task, TaskStatus
from domain.tasks.extract import REQUEST_PATTERNS
from security.masking import mask

DEFAULT_FOLLOWUP_DAYS: Final = 2
QUESTION_PATTERN: Final = re.compile(
    r"[?？]|문의|여쭙|회신\s?(?:부탁|바랍|주시|요청)|답변\s?(?:부탁|바랍|주시|요청)|"
    r"알려\s?주(?:세요|시기|실)|확인\s?(?:해\s?주|부탁|바랍|요청)"
)
MAX_SCAN_CHARS: Final = 4000  # 질문 표현을 찾을 본문 앞부분(메모리에서만)
# 회신 대기를 보여 줄 상태(제안·무시·완료는 보이지 않는다)
OPEN_STATUSES: Final = frozenset(
    {TaskStatus.WAITING, TaskStatus.IN_PROGRESS, TaskStatus.DONE_CANDIDATE, TaskStatus.ON_HOLD}
)
NONE: Final = "none"
WAITING: Final = "waiting"
FOLLOWUP: Final = "followup"
MAX_TITLE_CHARS: Final = 120


def asks_reply(subject: str, body: str) -> bool:
    """질문·요청 표현이 있는지(제목 + 본문 앞부분, 메모리에서만 판정)."""
    text = f"{subject}\n{body[:MAX_SCAN_CHARS]}"
    return bool(QUESTION_PATTERN.search(text) or REQUEST_PATTERNS.search(text))


def _direction(ref: MailRef, thread: MailThread) -> str | None:
    if ref.direction is not None:
        return ref.direction
    for message in thread.messages:
        if message.key.message_id == ref.message_id:
            return MAIL_OUT if message.sent_by_me else MAIL_IN
    return None


def sync_awaiting(task: Task, thread: MailThread, refs: Sequence[MailRef]) -> Task:
    """수집한 스레드로 회신 대기 표시를 갱신한다. 판단할 수 없으면 그대로 둔다."""
    if task.status in (TaskStatus.DONE, TaskStatus.DISMISSED) or not refs:
        return task
    last = max(refs, key=lambda r: r.received_at)
    direction = _direction(last, thread)
    if direction == MAIL_IN:
        # 상대 회신이 마지막 → 자동 해제
        if task.awaiting_reply_since is None and not task.followup_dismissed:
            return task
        return replace(task, awaiting_reply_since=None, followup_dismissed=False)
    if direction != MAIL_OUT:
        return task
    message = next((m for m in thread.messages if m.key.message_id == last.message_id), None)
    if message is None:
        return task  # 이번 수집에 본문이 없으면(지난 수집분) 판단을 바꾸지 않는다
    if not asks_reply(message.subject, message.body):
        if task.awaiting_reply_since is None or task.awaiting_reply_since == last.received_at:
            return replace(task, awaiting_reply_since=None, followup_dismissed=False)
        return task  # 사용자가 직접 표시한 대기는 유지
    if task.awaiting_reply_since == last.received_at:
        return task  # 같은 메일: 해제 여부 유지
    return replace(task, awaiting_reply_since=last.received_at, followup_dismissed=False)


@dataclass(frozen=True, slots=True)
class FollowupState:
    state: str  # none | waiting | followup
    since: datetime | None = None
    business_days: int = 0


@dataclass(frozen=True, slots=True)
class FollowupPolicy:
    days: int = DEFAULT_FOLLOWUP_DAYS
    holidays: frozenset[date] = frozenset()

    def state(self, task: Task, now: datetime) -> FollowupState:
        since = task.awaiting_reply_since
        if since is None or task.followup_dismissed or task.status not in OPEN_STATUSES:
            return FollowupState(NONE)
        elapsed = business_days_after(since, now, self.holidays)
        return FollowupState(FOLLOWUP if elapsed >= self.days else WAITING, since, elapsed)


def followup_draft(title: str, business_days: int) -> str:
    """팔로업 메일 초안 문안. 업무 제목(마스킹)과 경과 근무일만 쓴다(원문·이름·주소 없음)."""
    clean = mask(" ".join(title.split())).value[:MAX_TITLE_CHARS] or "요청 건"
    when = f"{business_days}영업일 전" if business_days > 0 else "앞서"
    return (
        f"제목: [확인 요청] {clean}\n\n"
        "안녕하세요.\n"
        f"{when} 문의드린 '{clean}' 건의 진행 상황을 확인 부탁드립니다.\n"
        "확인이 어려우시면 예상 일정을 알려 주시면 감사하겠습니다.\n\n"
        "감사합니다."
    )
