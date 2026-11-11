"""AI 호출 관문(AI-02·03·06, AI-08).

- 전송 직전에 인자가 `MaskedText`인지 다시 검사하고,
  재마스킹해도 바뀌지 않는지(마스킹 누락 없음) 확인한다.
- 요약 길이를 제한해 메일 원문 전체가 나가지 않게 한다(AI-02).
- 시간 초과·재시도·실패 안내를 처리하고, 호출 이력(시각·마스킹 요약·성공/실패)만 기록한다(AI-06).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from typing import Final, TypeVar

from domain.ai import AiAnswer, AiUnavailableError, Classification, KnowledgeAI
from domain.models import TaskEventType
from domain.ports import Clock, EventRecorder
from security.links import safe_link
from security.logging_policy import EventType, Site, log_event
from security.masking import MaskedText, mask, require_masked
from storage.knowledge_repos import AiCallRecord, SqliteAiCallLog

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_SUMMARY_CHARS: Final = 2000
MAX_SIMILAR_CASES: Final = 5
MAX_CASE_CHARS: Final = 1000
TIMEOUT_MESSAGE: Final = "AI 응답이 늦어 중단했습니다. 잠시 후 다시 시도해 주세요."
FAILURE_MESSAGE: Final = "AI 호출에 실패했습니다. 잠시 후 다시 시도해 주세요."


class PayloadRejectedError(ValueError):
    """전송 금지 내용(마스킹 누락, 길이 초과)."""


def check_payload(value: object, limit: int) -> MaskedText:
    masked = require_masked(value)
    if len(masked) > limit:
        raise PayloadRejectedError("AI로 보낼 요약이 너무 깁니다(메일 원문 전체 전송 금지)")
    if mask(masked.value).value != masked.value:
        raise PayloadRejectedError("마스킹되지 않은 민감정보가 남아 있습니다")
    return masked


class AiGateway:
    def __init__(
        self,
        adapter: KnowledgeAI,
        call_log: SqliteAiCallLog,
        events: EventRecorder,
        clock: Clock,
        *,
        timeout_seconds: float,
        retries: int = 1,
        link_bases: tuple[str, ...] = (),
    ) -> None:
        # 답변의 근거 링크로 허용할 사이트(AI-Ground·G-Knowledge·위키 기준 주소)
        self._link_bases = link_bases
        self.adapter = adapter
        self._log = call_log
        self._events = events
        self._clock = clock
        self._timeout = timeout_seconds
        self._retries = max(0, retries)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="smartmail-ai")

    def _call(self, summary: MaskedText, func: Callable[[], T], task_id: int | None) -> T:
        last_error: Exception | None = None
        for _attempt in range(self._retries + 1):
            future = self._executor.submit(func)
            try:
                result = future.result(timeout=self._timeout)
            except FutureTimeout:
                future.cancel()
                last_error = AiUnavailableError(TIMEOUT_MESSAGE)
            except AiUnavailableError as exc:
                last_error = exc
                break  # 규격 미확인·로그인 필요는 재시도해도 같다
            except Exception:
                logger.warning("AI 어댑터 오류: adapter=%s", self.adapter.name)
                last_error = AiUnavailableError(FAILURE_MESSAGE)
            else:
                self._record(summary, True, task_id)
                return result
        self._record(summary, False, task_id)
        assert last_error is not None  # noqa: S101
        raise last_error

    def _record(self, summary: MaskedText, success: bool, task_id: int | None) -> None:
        self._log.add(AiCallRecord(self._clock.now(), summary, success))
        self._events.record(
            TaskEventType.AI_CALLED, task_id=task_id, detail="success" if success else "failure"
        )
        log_event(EventType.AI_CALL, Site.AIGROUND, success)

    def classify_task(self, summary: MaskedText, task_id: int | None = None) -> Classification:
        checked = check_payload(summary, MAX_SUMMARY_CHARS)
        return self._call(checked, lambda: self.adapter.classify_task(checked), task_id)

    def suggest_solution(
        self,
        summary: MaskedText,
        similar_cases: Sequence[MaskedText] = (),
        task_id: int | None = None,
    ) -> AiAnswer:
        checked = check_payload(summary, MAX_SUMMARY_CHARS)
        cases = [check_payload(c, MAX_CASE_CHARS) for c in similar_cases[:MAX_SIMILAR_CASES]]
        answer = self._call(checked, lambda: self.adapter.suggest_solution(checked, cases), task_id)
        links = tuple(link for r in answer.references if (link := safe_link(r, self._link_bases)))
        return replace(answer, references=links)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
