"""G-Knowledge 지식 저장소 인터페이스와 등록 초안(GK-01~07, RET-03).

등록 초안의 모든 문자열은 `MaskedText`다. 초안은 `build_draft`/`draft_from_edit`로만 만들며,
사용자가 미리보기에서 고친 값도 다시 마스킹한다(GK-07).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol

from domain.clock import KST
from domain.errors import UserInputError
from domain.models import HistoryCard, Task
from security.masking import MaskedText, mask, require_masked

MAX_TITLE: Final = 200
MAX_BODY: Final = 8000
MAX_TAGS: Final = 10
MAX_TAG_CHARS: Final = 30


class KnowledgeUnavailableError(Exception):
    """G-Knowledge 사용 불가(필드 미확인, 로그인 필요, 제출 실패). 메시지는 사용자 안내용."""


class RegisterNotSentError(KnowledgeUnavailableError):
    """등록요청을 누르기 **전에** 멈춤(로그인 필요·입력 확인·분석 시간 초과 등, 결정기록 0035).

    G-Knowledge에 아무것도 보내지 않았으므로 다시 시도해도 중복 등록이 생기지 않는다.
    code는 화면 문구로 바꾸는 코드 값이다(메시지·개인정보를 담지 않음).
    """

    def __init__(self, message: str, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


class RegisterResultUnknownError(KnowledgeUnavailableError):
    """등록요청을 누른 **뒤** 결과를 확인하지 못함(결정기록 0035). 자동으로 다시 보내지 않는다."""


# 등록 진행 단계(화면 문구: 화면 여는 중 / AI 분석 중 / 등록요청 중)
STEP_OPEN: Final = "open"
STEP_ANALYZE: Final = "analyze"
STEP_SUBMIT: Final = "submit"
StepCallback = Callable[[str], None]


class DuplicateRegistrationError(Exception):
    """이미 등록된 업무(GK-06). 재등록은 명시적 요청으로만 한다."""


@dataclass(frozen=True, slots=True)
class MaskedDraft:
    title: MaskedText
    body: MaskedText
    tags: tuple[MaskedText, ...]

    def __post_init__(self) -> None:
        # mypy를 거치지 않은 코드가 str을 넣어도 실행 시점에 막는다(보안 검토 L-7).
        for value in (self.title, self.body, *self.tags):
            require_masked(value)


@dataclass(frozen=True, slots=True)
class RegisterResult:
    link: str


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    title: str
    link: str
    snippet: str = ""


class KnowledgeStore(Protocol):
    name: str

    def register(
        self, draft: MaskedDraft, on_step: StepCallback | None = None
    ) -> RegisterResult: ...

    def search(self, query: MaskedText) -> list[KnowledgeHit]: ...


def _mask_cut(text: str, limit: int) -> MaskedText:
    """마스킹한 뒤 자른다(잘린 경계에 민감정보 일부가 남지 않게)."""
    return mask(mask(text).value[:limit])


def _clean_tags(tags: tuple[str, ...] | list[str]) -> tuple[MaskedText, ...]:
    cleaned = [t.strip()[:MAX_TAG_CHARS] for t in tags if t.strip()]
    return tuple(mask(t) for t in cleaned[:MAX_TAGS])


def draft_from_edit(title: str, body: str, tags: list[str]) -> MaskedDraft:
    """미리보기에서 사용자가 고친 값을 다시 마스킹해 초안으로 만든다."""
    if not title.strip() or not body.strip():
        raise UserInputError("제목과 본문을 입력해 주세요")
    if len(title) > MAX_TITLE or len(body) > MAX_BODY:
        raise UserInputError("제목 또는 본문이 너무 깁니다")
    return MaskedDraft(mask(title.strip()), mask(body.strip()), _clean_tags(tags))


def build_draft(
    task: Task,
    card: HistoryCard | None,
    assignee: str,
    processed_at: datetime | None,
) -> MaskedDraft:
    """업무·히스토리 카드로 등록 초안을 만든다(GK-02). R&R 항목을 포함한다(RET-03)."""
    problem = card.problem if card else task.title
    cause = card.cause if card else "(입력 필요)"
    action = card.action if card else "(입력 필요)"
    processed = processed_at or task.closed_at
    processed_label = processed.astimezone(KST).strftime("%Y-%m-%d") if processed else "-"
    lines = [
        "■ 문제",
        problem,
        "",
        "■ 원인",
        cause,
        "",
        "■ 조치(처리 방법)",
        action,
        "",
        "■ R&R",
        f"- 업무명: {task.title}",
        f"- 담당자: {assignee}",
        f"- 요청 부서: {task.requester_dept or '-'}",
        f"- 처리일: {processed_label}",
        f"- 업무 구분: {task.module_tag or '-'}",
    ]
    if card and card.tcode:
        lines.append(f"- T-code/프로그램: {card.tcode}")
    tags = list(card.tags) if card else []
    if task.module_tag and task.module_tag not in tags:
        tags.insert(0, task.module_tag)
    return MaskedDraft(
        _mask_cut(task.title, MAX_TITLE), _mask_cut("\n".join(lines), MAX_BODY), _clean_tags(tags)
    )
