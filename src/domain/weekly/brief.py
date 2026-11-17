"""주간보고 업무별 간략 요약(WIKI-01·02, 결정기록 0042). 순수 로직.

메일 원문은 읽지 않는다. 메일을 수집할 때 AI가 메일 본문을 읽고 만든 **마스킹된 요약**만 쓴다.

- 업무 진행 요약(`Task.progress_digest`)
- 메일별 구조화 요약(`MailRef.digest`: 요청 사항·기한·필요 조치)
- 메일 방향·시각

출력은 서술형이 아니라 짧은 개조식이다(예: "결산 조정 데이터 검증 완료", "회신 대기 · 11/16 보냄").
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from domain.clock import KST
from domain.models import MAIL_OUT, STATUS_LABELS, MailRef, Task, TaskStatus
from security.masking import mask

MAX_BRIEF_CHARS: Final = 60  # 수행 내용 한 줄 상한(글자)
MAX_ETA_CHARS: Final = 20
MAX_PART_CHARS: Final = 40  # '요청 → 최근 후속' 각 부분 상한
FOLLOWUP_MARK: Final = "(후속)"  # 구조화 요약에서 후속 메일 요청 앞에 붙는 표시(결정기록 0021)
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s*")
_LABEL = re.compile(r"^\s*(?:요청 사항|필요 조치|진행|요약|현황)\s*:\s*")
_SENTENCE_END = re.compile(r"(?<=[다요음함됨])\.\s|(?<=[다요음함됨])\.$|\n")
# 서술형 끝맺음 → 개조식(명사형). 위에서부터 첫 일치만 바꾼다
# 과거형 줄임 글자 → 명사형(ㅕ+ㅆ → ㅣ+ㅁ)
_CONTRACTED: Final = dict(zip("혔였겼졌렸쳤켰녔셨", "힘임김짐림침킴님심", strict=True))
_ENDINGS: Final = (
    (re.compile(r"(?:요청|부탁)\s?드립니다$"), "요청"),
    (re.compile(r"(?:해|하여)\s?주(?:세요|십시오|시기 바랍니다)$"), " 요청"),
    (re.compile(r"(?:하였|했)습니다$|(?:하였|했)음$|(?:하였|했)어요$"), "함"),
    (re.compile(r"(?:되었|됐)습니다$|(?:되었|됐)음$"), "됨"),
    (re.compile(r"합니다$|해요$"), "함"),
    (re.compile(r"됩니다$|돼요$"), "됨"),
    (re.compile(r"있습니다$"), "있음"),
    (re.compile(r"없습니다$"), "없음"),
    (re.compile(r"입니다$|이에요$|예요$"), ""),
    (re.compile(r"(?<=[가-힣])(?:았|었)습니다$"), "음"),  # 받았습니다 → 받음
    (
        re.compile(r"(?<=[가-힣])([혔였겼졌렸쳤켰녔셨])습니다$"),
        lambda m: _CONTRACTED[m.group(1)],
    ),  # 막혔습니다 → 막힘
    (re.compile(r"(?:할|일|될)까요\??$"), " 문의"),
)
_WORDY = re.compile(r"[0-9A-Za-z가-힣]{2,}")  # 글자 없는 줄(표 구분선 '|---|' 등)은 버린다
_TRAIL = " .,;:·…"
_DIGEST_FIELD = re.compile(r"^(요청 사항|기한|필요 조치)\s*:\s*(.*)$")
_NO_DUE = ("언급 없음", "없음", "-", "")


@dataclass(frozen=True, slots=True)
class TaskBrief:
    """업무 하나의 주간보고용 요약(모두 마스킹된 짧은 글)."""

    work: str  # 수행 내용·다음 할 일(개조식). 근거가 없으면 빈 글자
    progress: str  # 진행 상황(상태 · 메일 흐름)
    eta: str  # 완료일(완료) 또는 완료 예정(기한)


EMPTY_BRIEF: Final = TaskBrief("", "", "-")


@dataclass(frozen=True, slots=True)
class DigestFields:
    requests: tuple[str, ...]
    due: str | None
    action: str | None


def parse_digest(text: str | None) -> DigestFields:
    """`RequestDigest.render()` 형식(요청 사항·기한·필요 조치)을 다시 나눈다."""
    requests: list[str] = []
    due: str | None = None
    action: str | None = None
    current = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        field = _DIGEST_FIELD.match(line)
        if field:
            current, value = field.group(1), field.group(2).strip()
            if current == "기한":
                due = value or None
            elif current == "필요 조치":
                action = value or None
            continue
        if current == "요청 사항" and line.startswith("-"):
            requests.append(line.lstrip("- ").strip())
    return DigestFields(tuple(r for r in requests if _WORDY.search(r)), due, action)


def concise(text: str | None, limit: int = MAX_BRIEF_CHARS) -> str:
    """첫 문장만 개조식으로 줄여 마스킹한다(서술형 끝맺음 제거, 상한 넘으면 …)."""
    source = (text or "").strip()
    if not source:
        return ""
    first = _SENTENCE_END.split(source, maxsplit=1)[0]
    line = _LABEL.sub("", _BULLET.sub("", " ".join(first.split())))
    line = line.removesuffix("...").rstrip(_TRAIL)
    if not _WORDY.search(line):
        return ""
    for pattern, repl in _ENDINGS:
        line, count = pattern.subn(repl, line)
        if count:
            break
    # 마스킹한 뒤에 자른다(경계에 걸린 민감정보 일부 노출 방지)
    line = mask(line.strip()).value
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _date(value: datetime) -> str:
    return value.astimezone(KST).strftime("%m/%d")


def _latest_digest(refs: Sequence[MailRef]) -> DigestFields:
    for ref in sorted(refs, key=lambda r: (r.received_at, r.id or 0), reverse=True):
        if ref.digest:
            return parse_digest(ref.digest)
    return DigestFields((), None, None)


def _work(task: Task, digest: DigestFields) -> str:
    """수행 내용: 진행 요약(스레드 전체) → 없으면 '요청 핵심 → 최근 후속' → 필요 조치."""
    progress = concise(task.progress_digest)
    if progress:
        return progress
    first = next((r for r in digest.requests if not r.startswith(FOLLOWUP_MARK)), None)
    latest = next((r for r in reversed(digest.requests) if r.startswith(FOLLOWUP_MARK)), None)
    if first or latest:
        head = concise(first, MAX_PART_CHARS) if first else ""
        tail = concise(latest.removeprefix(FOLLOWUP_MARK), MAX_PART_CHARS) if latest else ""
        return " → 최근: ".join(x for x in (head, tail) if x) if head else "최근: " + tail
    return concise(digest.action)


def _progress(task: Task, refs: Sequence[MailRef], now: datetime) -> str:
    parts = [STATUS_LABELS[task.status]]
    if task.status is not TaskStatus.DONE:
        if task.due_at is not None and task.due_at < now:
            parts.append("기한 지남")
        if task.awaiting_reply_since is not None and not task.followup_dismissed:
            parts.append("회신 대기")
    replies = sum(1 for r in refs if r.direction == MAIL_OUT)
    if replies:
        parts.append(f"내 회신 {replies}통")
    if refs:
        last = max(refs, key=lambda r: (r.received_at, r.id or 0))
        parts.append(
            f"{_date(last.received_at)} {'보냄' if last.direction == MAIL_OUT else '받음'}"
        )
    return " · ".join(parts)


def _eta(task: Task, digest: DigestFields) -> str:
    if task.status is TaskStatus.DONE:
        return f"{_date(task.closed_at)} 완료" if task.closed_at is not None else "완료"
    if task.due_at is not None:
        return _date(task.due_at)
    if digest.due and digest.due not in _NO_DUE:
        return concise(digest.due, MAX_ETA_CHARS)
    return "-"


def task_brief(task: Task, refs: Sequence[MailRef], now: datetime) -> TaskBrief:
    """업무 하나의 간략 요약. refs는 그 업무의 메일 참조(원문 없음)."""
    digest = _latest_digest(refs)
    return TaskBrief(_work(task, digest), _progress(task, refs, now), _eta(task, digest))
