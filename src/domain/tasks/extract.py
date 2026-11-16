"""메일 스레드에서 업무 후보 추출(TODO-01, 기획서 가설 H1).

1차는 규칙(받는 사람/참조, 요청 표현, 기한 표현)으로 판별한다. 애매한 경우에만 AI 분류를 쓰며,
AI에는 제목·근거 문장을 마스킹한 짧은 요약만 보낸다(AI-02·03). AI가 실패해도 규칙 결과로 진행한다.
근거 문장은 화면 표시용으로 메모리에서만 쓰고 저장하지 않는다(MAIL-08).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol

from domain.ai import AiUnavailableError, Classification
from domain.clock import KST
from domain.mail import MailMessage, MailThread
from domain.models import MAX_REASONS
from domain.tasks.mail_text import mail_excerpt
from security.masking import MaskedText, mask

REQUEST_PATTERNS: Final = re.compile(
    r"요청(?:드립|합니|드려|함)|부탁|해\s?주(?:세요|시기|실\s?수)|주시기\s?바랍|바랍니다|"
    r"가능할까요|가능하실까요|확인\s?(?:요청|바랍|부탁)|처리\s?(?:요청|바랍|부탁)|필요합니다|검토\s?바랍"
)
REQUEST_SUBJECT: Final = re.compile(r"\[(?:요청|문의|긴급)\]|요청|문의|부탁")
REPLY_PREFIX: Final = re.compile(
    r"^\s*(?:(?:re|fw|fwd|회신|전달)\s*:\s*|\[(?:요청|문의|긴급)\]\s*)+", re.I
)
DEPT_PATTERN: Final = re.compile(r"(\S+(?:팀|부|실|센터|본부|그룹))")
SENTENCE_SPLIT: Final = re.compile(r"(?<=[.!?。])\s+|\n+")

WEEKDAYS: Final = "월화수목금토일"
DUE_MD: Final = re.compile(
    r"(\d{1,2})[ \t]{0,2}[/.월][ \t]{0,2}(\d{1,2})[ \t]{0,2}일?[ \t]{0,2}(?:까지|중|전)"
)
DUE_RELATIVE: Final = re.compile(r"(오늘|금일|내일|명일|모레)[ \t]{0,2}(?:중|까지|안)")
DUE_WEEKDAY: Final = re.compile(
    r"(이번 ?주|금주|다음 ?주|차주)?[ \t]{0,2}([월화수목금])요일[ \t]{0,2}(?:까지|중|전)"
)

MAX_EVIDENCE_CHARS: Final = 200
MAX_DUE_SCAN: Final = 2000
MAX_TITLE_CHARS: Final = 200
SCORE_TASK: Final = 2  # 이 점수 이상이면 규칙만으로 후보
SCORE_AMBIGUOUS: Final = 1  # 이 점수면 AI 분류로 결정
AI_UNAVAILABLE_CONFIDENCE: Final = 0.35  # AI 판단 실패로 남긴 제안의 확신도(낮음 탭에 보임)
MAX_CLASSIFY_EXCERPT: Final = 400
MAX_CLASSIFY_CHARS: Final = 800


class TaskClassifier(Protocol):
    def classify_task(self, summary: MaskedText, task_id: int | None = None) -> Classification: ...


@dataclass(frozen=True, slots=True)
class TaskCandidate:
    title: str
    thread_id: str
    requested_at: datetime
    requester_dept: str | None
    due_at: datetime | None
    module_tag: str | None
    evidence: str  # 메모리 전용
    confidence: float
    reasons: tuple[str, ...] = ()  # 제안 이유 코드(결정기록 0015). 문장은 담지 않는다


def clean_title(subject: str, noise_prefixes: tuple[str, ...] = ()) -> str:
    """메일 제목에서 회신·전달 표시와 말머리([외부메일] 등)를 반복해서 지운다."""
    title = subject.strip()
    while True:
        before = title
        title = REPLY_PREFIX.sub("", title).strip()
        for prefix in noise_prefixes:
            if title.casefold().startswith(prefix.casefold()):
                title = title[len(prefix) :].strip()
        if title == before:
            break
    return (title or "(제목 없음)")[:MAX_TITLE_CHARS]


def requester_dept(sender_name: str) -> str | None:
    match = DEPT_PATTERN.search(sender_name)
    return match.group(1)[:100] if match else None


def _end_of_day(day: datetime) -> datetime:
    return day.replace(hour=23, minute=59, second=59, microsecond=0)


def parse_due(text: str, base: datetime) -> datetime | None:
    """기한 표현을 KST 그날 23:59:59로 바꾼다. 해석할 수 없으면 None."""
    local = base.astimezone(KST)
    match = DUE_MD.search(text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        for year in (local.year, local.year + 1):
            try:
                candidate = local.replace(year=year, month=month, day=day)
            except ValueError:
                return None
            if candidate.date() >= (local - timedelta(days=30)).date():
                return _end_of_day(candidate)
        return None
    match = DUE_RELATIVE.search(text)
    if match:
        offset = {"오늘": 0, "금일": 0, "내일": 1, "명일": 1, "모레": 2}[match.group(1)]
        return _end_of_day(local + timedelta(days=offset))
    match = DUE_WEEKDAY.search(text)
    if match:
        target = WEEKDAYS.index(match.group(2))
        days = (target - local.weekday()) % 7
        if match.group(1) and match.group(1).replace(" ", "") in ("다음주", "차주"):
            # 다음 주: 이번 주(월요일 시작) 다음 주의 해당 요일
            monday = local - timedelta(days=local.weekday())
            return _end_of_day(monday + timedelta(days=7 + target))
        return _end_of_day(local + timedelta(days=days))
    return None


def _evidence(message: MailMessage) -> tuple[str, int]:
    """요청 표현이 있는 첫 문장과 요청 표현 점수."""
    for sentence in SENTENCE_SPLIT.split(message.body):
        if REQUEST_PATTERNS.search(sentence):
            return sentence.strip()[:MAX_EVIDENCE_CHARS], 1
    return "", 0


def _with_flag(codes: tuple[str, ...], flag: str | None) -> tuple[str, ...]:
    """이유 코드 상한(MAX_REASONS)을 지키며 표시 코드를 덧붙인다."""
    return codes if flag is None else (*codes[: MAX_REASONS - 1], flag)


class Categorizer(Protocol):
    def categorize(self, subject: str, sender_address: str) -> str: ...

    def is_approval_category(self, value: str) -> bool: ...


class TaskExtractor:
    def __init__(
        self,
        classifier: TaskClassifier | None = None,
        categorizer: Categorizer | None = None,
        noise_prefixes: tuple[str, ...] = (),
    ) -> None:
        self._classifier = classifier
        self._categorizer = categorizer
        self._noise = noise_prefixes

    def title_of(self, subject: str) -> str:
        return clean_title(subject, self._noise)

    def score(self, message: MailMessage) -> tuple[int, str]:
        evidence, body_hit = _evidence(message)
        points = body_hit
        points += 1 if REQUEST_SUBJECT.search(message.subject) else 0
        points += 1 if message.to_me else 0
        if not message.to_me and not message.cc_me:
            points -= 1  # 단체 발송·참조 외 수신
        return points, evidence

    def _ai_says_task(self, message: MailMessage, evidence: str) -> Classification | None:
        if self._classifier is None:
            return None
        # 근거 문장만으로는 판단이 어려워 본문 앞부분(서명·인사 제외)을 함께 보낸다(결정기록 0021).
        # 마스킹한 뒤 자르고 다시 마스킹 타입으로 만든다(경계 노출 방지)
        excerpt = " ".join(mail_excerpt(message.body, MAX_CLASSIFY_EXCERPT).splitlines())
        text = f"{self.title_of(message.subject)} / {evidence} / {excerpt}"
        summary = mask(mask(text).value[:MAX_CLASSIFY_CHARS])
        try:
            return self._classifier.classify_task(summary)
        except (AiUnavailableError, ValueError):
            return None

    def extract(self, thread: MailThread) -> TaskCandidate | None:
        received = [m for m in thread.messages if not m.sent_by_me]
        if not received:
            return None
        first = received[0]
        points, evidence = self.score(first)
        confidence = min(1.0, 0.4 + 0.2 * points)
        # 구분(일반업무·전자결재 등, 사용자 정의 목록). SAP 모듈 추정은 하지 않는다(결정기록 0014).
        category = (
            self._categorizer.categorize(first.subject, first.sender_address)
            if self._categorizer is not None
            else None
        )
        ai_judged = ai_unavailable = False
        if points < SCORE_TASK:
            if points < SCORE_AMBIGUOUS:
                return None
            verdict = self._ai_says_task(first, evidence)
            if verdict is None and self._classifier is not None:
                # AI 실패(로그인·호출 제한 등): 조용히 버리지 않고 확신도 낮은 제안으로 남긴다
                # (사용자가 제안 목록에서 판단, 결정기록 0021)
                confidence = AI_UNAVAILABLE_CONFIDENCE
                ai_unavailable = True
            elif verdict is None or not verdict.is_task:
                return None
            else:
                confidence = verdict.confidence
                ai_judged = True
        # 기한 탐색은 앞부분만(긴 본문으로 인한 처리 지연 방지)
        text_for_due = f"{first.subject}\n{first.body[:MAX_DUE_SCAN]}"
        due = parse_due(text_for_due, first.received_at)
        return TaskCandidate(
            title=self.title_of(first.subject),
            thread_id=thread.thread_id,
            requested_at=first.received_at,
            requester_dept=requester_dept(first.sender_name),
            due_at=due,
            module_tag=category,
            evidence=evidence or self.title_of(first.subject),
            confidence=confidence,
            reasons=_with_flag(
                self._reasons(thread, first, evidence, due, category, ai_judged),
                "ai_unavailable" if ai_unavailable else None,
            ),
        )

    def _reasons(
        self,
        thread: MailThread,
        first: MailMessage,
        evidence: str,
        due: datetime | None,
        category: str | None,
        ai_judged: bool,
    ) -> tuple[str, ...]:
        """제안 이유 코드(명세 3-2). 이미 계산한 신호만 쓰고 코드값만 돌려준다(최대 6개)."""
        codes: list[str] = []
        if evidence:
            codes.append("request_phrase")
        if REQUEST_SUBJECT.search(first.subject):
            codes.append("subject_request")
        if due is not None:
            codes.append("due_phrase")
        if first.to_me:
            codes.append("to_me")
        elif first.cc_me:
            codes.append("cc_only")
        if len(thread.messages) >= 2 and not any(m.sent_by_me for m in thread.messages):
            codes.append("thread_waiting")
        categorizer = self._categorizer
        if category and categorizer is not None and categorizer.is_approval_category(category):
            codes.append("approval")
        if ai_judged:
            codes.append("ai_judged")
        return tuple(codes[:MAX_REASONS])
