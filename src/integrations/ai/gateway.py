"""AI 호출 관문(AI-02·03·06, AI-08).

- 전송 직전에 인자가 `MaskedText`인지 다시 검사하고,
  재마스킹해도 바뀌지 않는지(마스킹 누락 없음) 확인한다.
- 요약 길이를 제한해 메일 원문 전체가 나가지 않게 한다(AI-02).
- 시간 초과·재시도·실패 안내를 처리하고, 호출 이력(시각·마스킹 요약·성공/실패)만 기록한다(AI-06).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from typing import Final, TypeVar

from domain.ai import (
    MAX_DIGEST_REQUESTS,
    AiAnswer,
    AiUnavailableError,
    Classification,
    KnowledgeAI,
    RequestDigest,
)
from domain.models import TaskEventType
from domain.ports import Clock, EventRecorder
from domain.tasks.requester import MAX_DOMAIN_CHARS, valid_company_name
from security.links import safe_link
from security.logging_policy import EventType, Site, log_event
from security.masking import MaskedText, mask, require_masked
from storage.knowledge_repos import AiCallRecord, SqliteAiCallLog

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_SUMMARY_CHARS: Final = 2000
MAX_SIMILAR_CASES: Final = 5
MAX_CASE_CHARS: Final = 1000
# 요청 요약: 한 번에 4건, 건당 마스킹된 제목·본문 발췌 1,500자(메일 원문 전체 전송 금지, AI-02).
# 전달 메일의 원래 요청·후속 메일을 함께 넣도록 늘리고, 대신 묶음 크기를 줄였다(결정기록 0021).
MAX_SUMMARY_BATCH: Final = 4
MAX_SUMMARY_ITEM_CHARS: Final = 1500
MAX_TITLE_CHARS: Final = 200
MAX_DIGEST_FIELD_CHARS: Final = 200
TIMEOUT_MESSAGE: Final = "AI 응답이 늦어 중단했습니다. 잠시 후 다시 시도해 주세요."
FAILURE_MESSAGE: Final = "AI 호출에 실패했습니다. 잠시 후 다시 시도해 주세요."
UNSUPPORTED_MESSAGE: Final = "이 AI 연결은 해당 기능을 지원하지 않습니다."
# 회사명 질의는 도메인 문자열 하나만(결정기록 0027 D1): 영문 소문자·숫자·하이픈·점
_DOMAIN_ONLY: Final = re.compile(r"^[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63}){1,10}$")
# 진행 요약(결정기록 0028): 메일 최대 30통, 통당 마스킹 요약 400자, 결과 1,000자
MAX_PROGRESS_ITEMS: Final = 30
MAX_PROGRESS_ITEM_CHARS: Final = 400
MAX_PROGRESS_CHARS: Final = 1000


class PayloadRejectedError(ValueError):
    """전송 금지 내용(마스킹 누락, 길이 초과)."""


def check_payload(value: object, limit: int) -> MaskedText:
    masked = require_masked(value)
    if len(masked) > limit:
        raise PayloadRejectedError("AI로 보낼 요약이 너무 깁니다(메일 원문 전체 전송 금지)")
    if mask(masked.value).value != masked.value:
        raise PayloadRejectedError("마스킹되지 않은 민감정보가 남아 있습니다")
    return masked


def _clean_digest(raw: RequestDigest | None) -> RequestDigest | None:
    """AI 답변 칸을 다시 마스킹하고 자른다. 제목이 비면 None."""
    if raw is None:
        return None

    def clean(value: str | None, limit: int = MAX_DIGEST_FIELD_CHARS) -> str | None:
        text = mask(value).value.strip()[:limit] if value else ""
        return text or None

    title = clean(raw.title, MAX_TITLE_CHARS)
    if title is None:
        return None
    requests = tuple(r for item in raw.requests[:MAX_DIGEST_REQUESTS] if (r := clean(item)))
    return RequestDigest(title, requests, clean(raw.due), clean(raw.action))


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

    def _call(self, label: str, func: Callable[[], T], task_id: int | None) -> T:
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
                self._record(label, True, task_id)
                return result
        self._record(label, False, task_id)
        assert last_error is not None  # noqa: S101
        raise last_error

    def _record(self, label: str, success: bool, task_id: int | None) -> None:
        # 호출 기록에는 메일 내용을 남기지 않고 호출 종류·건수만 남긴다(보안검토 0021 중3)
        self._log.add(AiCallRecord(self._clock.now(), mask(label), success))
        self._events.record(
            TaskEventType.AI_CALLED, task_id=task_id, detail="success" if success else "failure"
        )
        log_event(EventType.AI_CALL, Site.AIGROUND, success)

    def classify_task(self, summary: MaskedText, task_id: int | None = None) -> Classification:
        checked = check_payload(summary, MAX_SUMMARY_CHARS)
        return self._call("업무 분류", lambda: self.adapter.classify_task(checked), task_id)

    def suggest_solution(
        self,
        summary: MaskedText,
        similar_cases: Sequence[MaskedText] = (),
        task_id: int | None = None,
    ) -> AiAnswer:
        checked = check_payload(summary, MAX_SUMMARY_CHARS)
        cases = [check_payload(c, MAX_CASE_CHARS) for c in similar_cases[:MAX_SIMILAR_CASES]]
        answer = self._call(
            f"해결 방안(사례 {len(cases)}건)",
            lambda: self.adapter.suggest_solution(checked, cases),
            task_id,
        )
        links = tuple(link for r in answer.references if (link := safe_link(r, self._link_bases)))
        return replace(answer, references=links)

    def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
        """메일 요청 구조화 요약(결정기록 0014·0021). 항목마다 마스킹·길이를 다시 검사하고,
        답변의 모든 칸도 다시 마스킹해 길이를 제한한다(저장 전 마지막 관문)."""
        if not 1 <= len(items) <= MAX_SUMMARY_BATCH:
            raise PayloadRejectedError("요약 묶음 크기가 올바르지 않습니다")
        checked = [check_payload(item, MAX_SUMMARY_ITEM_CHARS) for item in items]
        answers = self._call(
            f"요청 요약 {len(checked)}건", lambda: self.adapter.summarize_requests(checked), None
        )
        return [
            _clean_digest(answers[index] if index < len(answers) else None)
            for index in range(len(checked))
        ]

    def summarize_requests_many(
        self, batches: Sequence[Sequence[MaskedText]]
    ) -> list[list[RequestDigest | None] | AiUnavailableError]:
        """여러 묶음을 동시에 요약한다(결정기록 0023). 묶음마다 마스킹·길이 재검사, 답변 재마스킹,
        호출 기록(성공/실패)을 단독 호출과 똑같이 거친다. 묶음별 결과 또는 오류를 돌려주고,
        AI를 아예 쓸 수 없으면(로그인 필요·호출 제한·시간 초과) AiUnavailableError를 올린다."""
        if not batches:
            return []
        checked: list[list[MaskedText]] = []
        for batch in batches:
            if not 1 <= len(batch) <= MAX_SUMMARY_BATCH:
                raise PayloadRejectedError("요약 묶음 크기가 올바르지 않습니다")
            checked.append([check_payload(item, MAX_SUMMARY_ITEM_CHARS) for item in batch])
        labels = [f"요청 요약 {len(batch)}건" for batch in checked]
        # 탭이 순번대로 시작되므로 마지막 탭까지 걸리는 시간을 더해 준다
        stagger = float(getattr(self.adapter, "send_interval_seconds", 0.0))
        timeout = self._timeout + stagger * len(checked)
        future = self._executor.submit(self._summarize_many_raw, checked)
        try:
            raw = future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            error: AiUnavailableError = AiUnavailableError(TIMEOUT_MESSAGE)
        except AiUnavailableError as exc:
            error = exc
        except Exception:
            logger.warning("AI 어댑터 오류: adapter=%s", self.adapter.name)
            error = AiUnavailableError(FAILURE_MESSAGE)
        else:
            return [
                self._finish_one(label, len(batch), item)
                for label, batch, item in zip(labels, checked, raw, strict=True)
            ]
        for label in labels:
            self._record(label, False, None)
        raise error

    def _summarize_many_raw(
        self, batches: list[list[MaskedText]]
    ) -> list[list[RequestDigest | None] | AiUnavailableError]:
        many = getattr(self.adapter, "summarize_many", None)
        if many is not None:
            return list(many(batches))
        # 동시 처리를 모르는 어댑터는 묶음을 차례로 보낸다
        out: list[list[RequestDigest | None] | AiUnavailableError] = []
        for batch in batches:
            try:
                out.append(self.adapter.summarize_requests(batch))
            except AiUnavailableError as exc:
                out.append(exc)
        return out

    def _finish_one(
        self, label: str, size: int, outcome: list[RequestDigest | None] | AiUnavailableError
    ) -> list[RequestDigest | None] | AiUnavailableError:
        if isinstance(outcome, AiUnavailableError):
            self._record(label, False, None)
            return outcome
        self._record(label, True, None)
        return [
            _clean_digest(outcome[index] if index < len(outcome) else None) for index in range(size)
        ]

    def company_name(self, domain: MaskedText) -> str | None:
        """도메인 → 회사명(결정기록 0027). 도메인 문자열만 보내고, 답은 마스킹·형식 검사한다."""
        checked = check_payload(domain, MAX_DOMAIN_CHARS)
        if not _DOMAIN_ONLY.match(checked.value):
            raise PayloadRejectedError("도메인 형식만 보낼 수 있습니다")
        namer = getattr(self.adapter, "company_name", None)
        if namer is None:
            raise AiUnavailableError(UNSUPPORTED_MESSAGE)
        raw = self._call("회사명 확인", lambda: namer(checked), None)
        return valid_company_name(mask(raw).value) if isinstance(raw, str) else None

    def summarize_progress(self, items: Sequence[MaskedText], task_id: int | None = None) -> str:
        """업무 진행 요약(결정기록 0028). 메일별 마스킹 요약만 보내고, 답도 다시 마스킹한다."""
        if not 1 <= len(items) <= MAX_PROGRESS_ITEMS:
            raise PayloadRejectedError("진행 요약 항목 수가 올바르지 않습니다")
        checked = [check_payload(item, MAX_PROGRESS_ITEM_CHARS) for item in items]
        summarizer = getattr(self.adapter, "summarize_progress", None)
        if summarizer is None:
            raise AiUnavailableError(UNSUPPORTED_MESSAGE)
        raw = self._call(f"진행 요약 {len(checked)}통", lambda: summarizer(checked), task_id)
        return mask(str(raw)).value[:MAX_PROGRESS_CHARS]

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
