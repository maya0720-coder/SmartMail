"""이력 조회와 해결 방안 제안: 앱(7일) → G-Knowledge → AI-Ground(SRCH-01~05, HIST-04·06).

결정기록 0021로 바꾼 흐름:
- 앱 이력과 G-Knowledge를 모두 찾은 뒤, 찾은 사례를 근거로 붙여 AI 해결 방안을 만든다
  (예전: 앞 단계에서 찾으면 멈춰 링크 목록만 보여 줌). suggest=False면 사례 찾기만 한다.
- AI에는 검색어(업무 제목)만이 아니라 업무 맥락(구조화 요약·메일 발췌)을 마스킹해 보낸다.
- start_from으로 앞 단계를 건너뛸 수 있다(SRCH-04).
- 해결 단계(가장 앞에서 결과가 나온 단계)는 이벤트와 지표 원장에 남긴다.
외부(G-Knowledge·AI)로 가는 검색어·맥락·사례는 모두 마스킹한다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from domain.ai import AiAnswer, AiUnavailableError
from domain.errors import UserInputError
from domain.history_service import HistoryHit, HistoryService
from domain.knowledge import KnowledgeHit, KnowledgeUnavailableError
from domain.knowledge_service import KnowledgeService
from domain.models import ResolutionStage, TaskEventType
from domain.ports import EventRecorder, LedgerRepository
from domain.search.context import history_case, knowledge_case
from security.masking import MaskedText, mask

StageName = Literal["app", "gk", "ai"]
STAGE_ORDER: Final[tuple[StageName, ...]] = ("app", "gk", "ai")
NO_SOLUTION: Final = "해결 방안 없음"
MAX_QUERY: Final = 200
MAX_APP_HITS: Final = 5
MAX_GK_HITS: Final = 10
MAX_CASES: Final = 5  # AI에 붙이는 사례 수(앱 3 + G-Knowledge 2 우선)


class SolutionAdvisor(Protocol):
    def suggest_solution(
        self,
        summary: MaskedText,
        similar_cases: Sequence[MaskedText] = (),
        task_id: int | None = None,
    ) -> AiAnswer: ...


ContextProvider = Callable[[int], "MaskedText | None"]


@dataclass
class StageResult:
    stage: StageName
    ran: bool = False
    app_hits: list[HistoryHit] = field(default_factory=list)
    gk_hits: list[KnowledgeHit] = field(default_factory=list)
    ai_answer: AiAnswer | None = None
    notice: str | None = None  # 실패·미연동 안내
    cases_used: int = 0  # AI 단계: 근거로 붙인 사례 수

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


def pick_cases(app_hits: Sequence[HistoryHit], gk_hits: Sequence[KnowledgeHit]) -> list[MaskedText]:
    """AI에 붙일 사례: 앱 이력 우선(최대 3), 남는 칸은 G-Knowledge."""
    cases = [history_case(hit) for hit in app_hits[:3]]
    cases += [knowledge_case(hit) for hit in gk_hits[: MAX_CASES - len(cases)]]
    return cases[:MAX_CASES]


class SearchPipeline:
    def __init__(
        self,
        history: HistoryService,
        knowledge: KnowledgeService,
        advisor: SolutionAdvisor,
        events: EventRecorder,
        ledger: LedgerRepository,
        context: ContextProvider | None = None,
    ) -> None:
        self._history = history
        self._knowledge = knowledge
        self._advisor = advisor
        self._events = events
        self._ledger = ledger
        self._context = context

    def search(
        self,
        query: str,
        *,
        task_id: int | None = None,
        start_from: StageName = "app",
        module: str | None = None,
        suggest: bool = True,
    ) -> SearchOutcome:
        query = query.strip()[:MAX_QUERY]
        if not query:
            raise UserInputError("검색어를 입력해 주세요")
        stages = {name: StageResult(name) for name in STAGE_ORDER}
        start = STAGE_ORDER.index(start_from)
        app, gk, ai = stages["app"], stages["gk"], stages["ai"]
        if start <= 0:
            app.ran = True
            app.app_hits = self._history.search(query, module=module)[:MAX_APP_HITS]
        if start <= 1:
            gk.ran = True
            try:
                gk.gk_hits = self._knowledge.search(mask(query))[:MAX_GK_HITS]
            except KnowledgeUnavailableError as exc:
                gk.notice = str(exc)
        if suggest:
            ai.ran = True
            self._suggest(ai, query, task_id, pick_cases(app.app_hits, gk.gk_hits))
        resolved = next(
            (_RESOLUTION[s.stage] for s in stages.values() if s.found),
            ResolutionStage.UNRESOLVED,
        )
        self._events.record(TaskEventType.SEARCH_RESOLVED, task_id=task_id, detail=resolved.value)
        if task_id is not None:
            self._ledger.set_resolution(task_id, resolved)
        return SearchOutcome(list(stages.values()), resolved)

    def _suggest(
        self, result: StageResult, query: str, task_id: int | None, cases: list[MaskedText]
    ) -> None:
        context = self._context(task_id) if self._context and task_id is not None else None
        summary = context or mask(query)
        try:
            result.ai_answer = self._advisor.suggest_solution(summary, cases, task_id)
            result.cases_used = len(cases)
        except (AiUnavailableError, ValueError) as exc:
            result.notice = str(exc)

    def feedback(self, task_id: int | None, helpful: bool) -> None:
        """가이드 유용성 평가(HIST-06)."""
        self._events.record(
            TaskEventType.FEEDBACK, task_id=task_id, detail="helpful" if helpful else "not_helpful"
        )
