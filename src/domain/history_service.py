"""업무 히스토리 카드(HIST-01~03·05).

- 완료 업무의 내 회신 메일에서 문제·원인·조치 초안을 만든다(HIST-02). 초안은 저장하지 않고,
  사용자가 확인해 저장할 때만 마스킹해서 저장한다.
- 키워드 + 필터(모듈·기간·태그) 검색(HIST-03). 앱에는 보관 기간(7일) 안의 카드만 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from domain.errors import UserInputError
from domain.mail_service import MailService
from domain.models import HistoryCard, Task, Visibility
from domain.ports import Clock, HistoryRepository
from domain.tasks.service import TaskService
from security.masking import mask

SENTENCE: Final = re.compile(r"(?<=[.!?。])\s+|\n+")
CAUSE_HINT: Final = re.compile(r"원인|때문|확인 결과|확인해 보니|으로 인해|로 인해")
ACTION_HINT: Final = re.compile(r"조치|처리|재처리|수정|반영|완료|오픈|부여|변경")
# 한글 조사가 바로 붙는 경우(OB52에서)도 찾도록 영숫자 경계만 본다.
TCODE: Final = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z]{2,4}\d{1,3}[A-Z]?|Z[A-Z0-9_]{3,20})(?![A-Za-z0-9])"
)
MAX_FIELD: Final = 2000
TCODE_WEIGHT: Final = 3  # T-code가 같으면 같은 화면·기능 문제일 가능성이 높다
# 검색어에서 뺄 일반 단어(업무 제목에 흔한 요청 표현)
STOPWORDS: Final = frozenset(
    {
        "요청",
        "문의",
        "확인",
        "건",
        "관련",
        "부탁",
        "처리",
        "검토",
        "요청드립니다",
        "부탁드립니다",
        "re",
        "fw",
        "fwd",
        "회신",
        "전달",
        "및",
        "의",
        "건의",
    }
)
_PARTICLE: Final = re.compile(r"(?:은|는|이|가|을|를|에|에서|의|로|으로|와|과|도)$")


def search_words(query: str) -> list[str]:
    """검색 단어: 소문자, 끝 조사·일반 단어 제거, 2자 이상, 최대 10개."""
    words: list[str] = []
    for raw in re.split(r"[\s/,·\[\]()]+", query.casefold()):
        word = raw.strip(".:;!?\"'")
        if len(word) > 2:
            word = _PARTICLE.sub("", word)
        if len(word) >= 2 and word not in STOPWORDS and word not in words:
            words.append(word)
    return words[:10]


@dataclass(frozen=True, slots=True)
class CardDraft:
    problem: str
    cause: str
    action: str
    tcode: str | None


@dataclass(frozen=True, slots=True)
class HistoryHit:
    card: HistoryCard
    task: Task | None


def _pick(sentences: list[str], pattern: re.Pattern[str]) -> str:
    for sentence in sentences:
        if pattern.search(sentence):
            return sentence.strip()[:MAX_FIELD]
    return ""


class HistoryService:
    def __init__(
        self, history: HistoryRepository, tasks: TaskService, mail: MailService, clock: Clock
    ) -> None:
        self._history = history
        self._tasks = tasks
        self._mail = mail
        self._clock = clock

    def draft(self, task_id: int) -> CardDraft:
        """회신 메일 본문에서 초안을 만든다. 원본이 없으면 제목만 채운다(MAIL-09)."""
        task = self._tasks.get(task_id)
        reply_body = ""
        for ref in reversed(self._mail.refs_for_task(task_id)):
            if ref.id is None:
                continue
            view = self._mail.view(ref.id)
            if view is not None and view.message is not None and view.message.sent_by_me:
                reply_body = view.message.body
                break
        sentences = [s for s in SENTENCE.split(reply_body) if s.strip()]
        tcode = TCODE.search(reply_body)
        return CardDraft(
            problem=task.title,
            cause=_pick(sentences, CAUSE_HINT),
            action=_pick(sentences, ACTION_HINT),
            tcode=tcode.group(0) if tcode else None,
        )

    def save(
        self,
        task_id: int,
        draft: CardDraft,
        tags: tuple[str, ...] = (),
        visibility: Visibility = Visibility.TEAM,
    ) -> HistoryCard:
        """사용자가 확인한 카드를 마스킹해 저장한다(HIST-01·02·05)."""
        self._tasks.get(task_id)
        if not draft.problem.strip() or not draft.action.strip():
            raise UserInputError("문제와 조치 내용을 입력해 주세요")
        card = HistoryCard(
            task_id=task_id,
            problem=mask(draft.problem[:MAX_FIELD]).value,
            cause=mask(draft.cause[:MAX_FIELD]).value,
            action=mask(draft.action[:MAX_FIELD]).value,
            created_at=self._clock.now(),
            tcode=mask(draft.tcode).value[:100] if draft.tcode else None,
            tags=tuple(mask(t.strip()).value[:30] for t in tags if t.strip() and "," not in t)[:10],
            visibility=visibility,
        )
        return self._history.add(card)

    def get(self, task_id: int) -> HistoryCard | None:
        return self._history.get_for_task(task_id)

    def search(
        self,
        query: str,
        *,
        module: str | None = None,
        tag: str | None = None,
        within_days: int | None = None,
    ) -> list[HistoryHit]:
        """키워드 + 필터 검색(HIST-03·04, SRCH-01).

        업무 제목을 그대로 검색어로 쓰므로 모든 단어 일치(AND)를 요구하지 않는다(결정기록 0021).
        단어마다 점수를 매기고(T-code 일치는 가중치), 단어 수에 맞는 최소 점수를 넘은 카드만
        점수 높은 순으로 돌려준다. 단어가 1~2개면 1개만 맞아도 된다.
        """
        words = search_words(query)
        since: datetime | None = None
        if within_days is not None:
            since = self._clock.now() - timedelta(days=within_days)
        need = 1 if len(words) <= 2 else 2
        scored: list[tuple[int, int, HistoryHit]] = []
        for order, card in enumerate(self._history.list_cards()):
            task = self._tasks_get(card.task_id)
            if module and (task is None or task.module_tag != module):
                continue
            if tag and tag not in card.tags:
                continue
            if since and card.created_at < since:
                continue
            text = " ".join(
                [card.problem, card.cause, card.action, card.tcode or "", " ".join(card.tags)]
                + ([task.title] if task else [])
            ).casefold()
            tcodes = {c.casefold() for c in TCODE.findall(f"{card.tcode or ''} {text.upper()}")}
            score = sum((TCODE_WEIGHT if w in tcodes else 1) for w in words if w in text)
            if not words or score >= need:
                scored.append((-score, order, HistoryHit(card, task)))
        return [hit for _, _, hit in sorted(scored, key=lambda item: item[:2])]

    def _tasks_get(self, task_id: int) -> Task | None:
        try:
            return self._tasks.get(task_id)
        except LookupError:
            return None
