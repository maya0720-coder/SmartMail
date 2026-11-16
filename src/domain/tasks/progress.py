"""업무 진행 경과(결정기록 0028). 순수 로직: 저장된 메일 참조(방향·시각·마스킹 요약)만 쓴다.

- 규칙 요약: '첫 요청(일시) → 후속 n통 → 내 회신 n통 → 마지막: 보냄/받음(일시)'.
- AI 진행 요약 입력: 메일마다 '방향 시각 요약 제목 / 구조화 요약 일부'(이미 마스킹된 값)만 담는다.
  메일 원문·보낸 사람 이름·주소는 담지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from domain.clock import KST
from domain.models import MAIL_OUT, SUMMARY_DONE, MailRef, Task, summary_state_of
from security.masking import MaskedText, mask

MAX_PROGRESS_REFS: Final = 30  # AI 진행 요약에 담는 최대 메일 수(최근 것 우선)
MAX_ITEM_CHARS: Final = 400
MAX_DIGEST_PART: Final = 240
DIRECTION_LABEL: Final = {"in": "받음", "out": "보냄"}


def _when(value: datetime) -> str:
    return value.astimezone(KST).strftime("%m-%d %H:%M")


def ordered(refs: Sequence[MailRef]) -> list[MailRef]:
    return sorted(refs, key=lambda r: (r.received_at, r.id or 0))


def ref_title(ref: MailRef) -> str | None:
    """타임라인에 보일 요약 제목: AI 요약이 끝난 메일만(제목 유지·원문 없음은 None)."""
    return ref.summary if summary_state_of(ref) == SUMMARY_DONE else None


def rule_summary(refs: Sequence[MailRef]) -> str | None:
    """규칙 기반 진행 요약 한 줄. 메일이 없으면 None."""
    items = ordered(refs)
    if not items:
        return None
    first, last = items[0], items[-1]
    followups = sum(1 for r in items[1:] if r.direction != MAIL_OUT)
    replies = sum(1 for r in items if r.direction == MAIL_OUT)
    parts = [f"첫 요청({_when(first.received_at)})"]
    if followups:
        parts.append(f"후속 {followups}통")
    if replies:
        parts.append(f"내 회신 {replies}통")
    if len(items) > 1:
        direction = DIRECTION_LABEL.get(last.direction or "", "메일")
        parts.append(f"마지막: {direction}({_when(last.received_at)})")
    return " → ".join(parts)


def progress_items(refs: Sequence[MailRef]) -> list[MaskedText]:
    """AI 진행 요약 입력. 최근 MAX_PROGRESS_REFS통만, 시간순으로 담는다."""
    items = ordered(refs)[-MAX_PROGRESS_REFS:]
    result = []
    for ref in items:
        direction = DIRECTION_LABEL.get(ref.direction or "", "메일")
        title = ref_title(ref) or "(요약 없음)"
        line = f"{direction} {_when(ref.received_at)} {title}"
        if ref.digest:
            line += " / " + " ".join(ref.digest.split())[:MAX_DIGEST_PART]
        # 저장값도 마스킹된 값이지만, 보내기 직전에 다시 마스킹하고 자른 뒤 다시 확인한다
        result.append(mask(mask(line).value[:MAX_ITEM_CHARS]))
    return result


def progress_stale(task: Task, refs: Sequence[MailRef]) -> bool:
    """AI 진행 요약 뒤에 메일 수가 바뀌었는지('새 메일 있음 · 다시 요약')."""
    return task.progress_digest is not None and task.progress_ref_count != len(refs)
