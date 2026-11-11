"""지식 AI 인터페이스(AI-01~04, AI-08).

인자는 `MaskedText`만 받는다(AI-02·03). 마스킹하지 않은 `str`을 넘기면 mypy가 실패하고,
런타임에서도 `AiGateway`가 `require_masked`로 다시 검사한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from security.masking import MaskedText

AI_LABEL: Final = "AI 생성 · 검증 필요"


class AiUnavailableError(Exception):
    """AI 호출 불가(규격 미확인, 로그인 필요, 시간 초과, 오류 응답). 메시지는 사용자 안내용."""


@dataclass(frozen=True, slots=True)
class Classification:
    is_task: bool
    module_tag: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class AiAnswer:
    """AI 답변(AI-04). 화면에 낼 때는 항상 출력 인코딩하고 `label`을 함께 보여준다."""

    text: str
    references: tuple[str, ...] = ()
    label: str = AI_LABEL


class KnowledgeAI(Protocol):
    name: str

    def classify_task(self, summary: MaskedText) -> Classification: ...

    def suggest_solution(
        self, summary: MaskedText, similar_cases: Sequence[MaskedText]
    ) -> AiAnswer: ...
