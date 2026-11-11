"""3단계 이력 조회: 앱(7일) → G-Knowledge → AI-Ground g-llm(SRCH-01~05, HIST-06).

앞 단계에서 찾으면 멈춘다. 사용자는 다음 단계를 강제로 실행할 수 있다(SRCH-04).
외부(G-Knowledge·AI)로 가는 검색어·사례는 모두 마스킹한다. 해결 단계는 이벤트와 지표 원장에 남긴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from domain.ai import AiAnswer, AiUnavailableError
from domain.errors import UserInputError
from domain.history_service import HistoryHit, HistoryService
from domain.knowledge import KnowledgeHit, KnowledgeUnavailableError
from domain.knowledge_service import KnowledgeService
from domain.models import ResolutionStage, TaskEventType
from domain.ports import EventRecorder, LedgerRepository
from security.masking import MaskedText, mask

StageName = Literal["app", "gk", "ai"]
STAGE_ORDER: Final[tuple[StageName, ...]] = ("app", "gk", "ai")
NO_SOLUTION: Final = "해결 방안 없음"
MAX_QUERY: Final = 200


class SolutionAdvisor(Protocol):
    def suggest_solution(
        self,
        summary: MaskedText,
        similar_cases: Sequence[MaskedText] = (),
        task_id: int | None = None,
    ) -> AiAnswer: ...


@dataclass
class StageResult:
    stage: StageName
    ran: bool = False
    app_hits: list[HistoryHit] = field(default_factory=list)
    gk_hits: list[KnowledgeHit] = field(default_factory=list)
    ai_answer: AiAnswer | None = None
    notice: str | None = None  # 실패·미연동 안내

    @property
    def found(self) -> bool:
        return bool(self.app_hits or self.gk_hits or self.ai_answer)


@dataclass
class SearchOutcome:
    stages: list[StageResult]
    resolved: ResolutionStage

    @property
    def message(self) -> str | None:
        return NO_SOLUTION if self.resolved is ResolutionStage.UNRESOLVED else None


_RESOLUTION = {
    "app": ResolutionStage.APP,
    "gk": ResolutionStage.GKNOWLEDGE,
    "ai": ResolutionStage.AI,
}


class SearchPipeline:
    def __init__(
        self,
        history: HistoryService,
        knowledge: KnowledgeService,
        advisor: SolutionAdvisor,
        events: EventRecorder,
        ledger: LedgerRepository,
    ) -> None:
        self._history = history
        self._knowledge = knowledge
        self._advisor = advisor
        self._events = events
        self._ledger = ledger

    def search(
        self,
        query: str,
        *,
        task_id: int | None = None,
        start_from: StageName = "app",
        module: str | None = None,
    ) -> SearchOutcome:
        query = query.strip()[:MAX_QUERY]
        if not query:
            raise UserInputError("검색어를 입력해 주세요")
        stages = [StageResult(name) for name in STAGE_ORDER]
        resolved = ResolutionStage.UNRESOLVED
        start = STAGE_ORDER.index(start_from)
        for result in stages[start:]:
            result.ran = True
            self._run(result, query, task_id, module)
            if result.found:
                resolved = _RESOLUTION[result.stage]
                break
        self._events.record(TaskEventType.SEARCH_RESOLVED, task_id=task_id, detail=resolved.value)
        if task_id is not None:
            self._ledger.set_resolution(task_id, resolved)
        return SearchOutcome(stages, resolved)

    def _run(
        self, result: StageResult, query: str, task_id: int | None, module: str | None
    ) -> None:
        if result.stage == "app":
            result.app_hits = self._history.search(query, module=module)[:5]
        elif result.stage == "gk":
            try:
                result.gk_hits = self._knowledge.search(mask(query))[:10]
            except KnowledgeUnavailableError as exc:
                result.notice = str(exc)
        else:
            try:
                result.ai_answer = self._advisor.suggest_solution(mask(query), (), task_id)
            except (AiUnavailableError, ValueError) as exc:
                result.notice = str(exc)

    def feedback(self, task_id: int | None, helpful: bool) -> None:
        """가이드 유용성 평가(HIST-06)."""
        self._events.record(
            TaskEventType.FEEDBACK, task_id=task_id, detail="helpful" if helpful else "not_helpful"
        )
