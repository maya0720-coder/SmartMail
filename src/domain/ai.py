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


class AiParseError(AiUnavailableError):
    """AI가 답은 했지만 정해진 형식으로 해석하지 못함(묶음을 1건씩 다시 보낼 수 있음)."""


MAX_DIGEST_REQUESTS: Final = 5


@dataclass(frozen=True, slots=True)
class RequestDigest:
    """메일 1건의 구조화 요약(결정기록 0021). 값은 모두 마스킹된 AI 요약이다(원문 아님).

    title: 업무 목록 제목(40자 안팎), requests: 요청 사항(최대 5개),
    due: 기한 표현(없으면 None), action: 담당자가 할 일(필요 조치).
    """

    title: str
    requests: tuple[str, ...] = ()
    due: str | None = None
    action: str | None = None

    def render(self) -> str:
        """저장·화면용 텍스트(고정 머리말 줄). 제목은 업무 제목에 따로 저장한다."""
        lines = ["요청 사항:"] + [f"- {item}" for item in self.requests[:MAX_DIGEST_REQUESTS]]
        lines.append(f"기한: {self.due or '언급 없음'}")
        if self.action:
            lines.append(f"필요 조치: {self.action}")
        return "\n".join(lines)


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

    def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
        """메일 여러 건(마스킹된 제목·본문 발췌)을 구조화 요약한다(결정기록 0014·0021).

        입력 순서대로 돌려준다. 해석하지 못한 항목은 None.
        """
        ...
