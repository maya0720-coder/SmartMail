"""메일 업무의 구조화 요약 일괄 처리(TODO-01 개정, 결정기록 0014·0021).

- AI(AI-Ground)가 메일 본문에서 제목·요청 사항·기한·필요 조치를 뽑는다. 제목은 업무 제목으로,
  나머지는 마스킹된 구조화 요약(mail_ref.digest)으로 저장한다. 메일 원문은 저장하지 않는다(MAIL-08).
- 요약할 때만 원본을 다시 읽어(메모리) 인사말·서명을 빼고, 전달 메일이면 원래 메일의 요청을,
  같은 업무에 후속 메일이 있으면 그 내용도 붙인다. 마스킹한 뒤 길이를 제한해 보낸다(AI-02·03).
- 실패 처리: 묶음 답변을 해석하지 못하면 1건씩 다시 보낸다. 1건 실패·일반 문구 답변은 횟수를
  세고, 상한에 닿으면 메일 제목 정리본을 유지하고 더 보내지 않는다(호출 상한 낭비 방지).
- AI를 쓸 수 없으면(로그인 필요·호출 제한) 멈추고, 다음 실행에서 이어서 한다.
- 병렬 처리(결정기록 0023): 묶음을 동시성 제한(`concurrency`)만큼 모아 한 번에 AI 어댑터로 넘긴다.
  원본 메일 읽기는 이 스레드에서 미리 하고(메일 접근은 기존 경로 그대로), AI 호출만 동시에 진행한다.
  호출 상한은 어댑터가 지키므로 병렬은 상한 안에서 기다리는 시간만 줄인다.
  실행 중인 항목은 추적해 같은 항목을 중복 전송하지 않는다.
- 요약 상태(결정기록 0029): 성공 done, 실패 상한·일반 문구 kept, 원본 없음 missing을 기록한다.
  보내는 중(running)은 메모리에서만 추적한다(`running_task_ids`).
- 후속 메일(결정기록 0028 D4): 받은 후속 메일도 첫 메일 뒤 순서(낮은 우선순위)로 요약한다.
  업무 제목은 바꾸지 않고 구조화 요약과 요약 제목만 저장한다. 방향을 모르는 메일은 건너뛴다.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from domain.ai import AiParseError, AiUnavailableError, RequestDigest
from domain.mail_service import MailService
from domain.models import (
    MAIL_IN,
    SUMMARY_DONE,
    SUMMARY_KEPT,
    SUMMARY_MISSING,
    MailRef,
    Task,
    TaskSource,
    TaskStatus,
)
from domain.ports import MailRefRepository
from domain.tasks.mail_text import QUOTE_START, mail_excerpt
from security.masking import MaskedText, mask

__all__ = ["QUOTE_START", "SummaryService", "TitleRules", "body_excerpt"]  # 기존 import 호환

BATCH_SIZE: Final = 4
MAX_ITEM_CHARS: Final = 1500
MAX_BODY_EXCERPT: Final = 1000
MAX_FOLLOWUP_EXCERPT: Final = 300
MAX_SUMMARY_TRIES: Final = 3  # 해석 실패 상한
MAX_GENERIC_RETRIES: Final = 1  # 일반 문구 답변이면 한 번 더 요청


class RequestSummarizer(Protocol):
    def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]: ...


class SummaryTasks(Protocol):
    def list_tasks(self, statuses: list[TaskStatus] | None = None) -> list[Task]: ...

    def set_summary_title(self, task_id: int, title: str) -> None: ...

    def title_of(self, subject: str) -> str: ...


@dataclass(frozen=True, slots=True)
class TitleRules:
    """업무 제목 규칙(결정기록 0015 · 점검 D1): 일반 문구뿐이거나 짧은 요약은 버린다."""

    min_chars: int = 8
    generic_phrases: tuple[str, ...] = ()
    polite_endings: tuple[str, ...] = ()

    def is_generic(self, summary: str) -> bool:
        compact = _normalize(summary)
        if len(compact) < self.min_chars:
            return True
        rest = compact
        for phrase in sorted(self.generic_phrases, key=len, reverse=True):
            rest = rest.replace(_normalize(phrase), "")
        for ending in sorted(self.polite_endings, key=len, reverse=True):
            rest = rest.replace(_normalize(ending), "")
        # 일반 문구·어미를 지우고 남는 내용이 거의 없으면(4자 미만) 일반 문구로 본다
        return len(rest) < 4


def _normalize(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text).casefold()


# 진행 알림: (이번 실행의 전체 건수, 처리 끝난 건수)
ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class SummaryRun:
    done: int
    failed: int
    remaining: int
    message: str | None = None
    kept: int = 0  # 일반 문구·반복 실패라 메일 제목을 유지한 건수


def body_excerpt(body: str) -> str:
    """회신 기록 앞까지의 본문을 한 줄로 줄여서 돌려준다(기존 호출 호환)."""
    return " ".join(mail_excerpt(body, MAX_BODY_EXCERPT).splitlines())[:MAX_BODY_EXCERPT]


class SummaryService:
    def __init__(
        self,
        tasks: SummaryTasks,
        refs: MailRefRepository,
        mail: MailService,
        ai: RequestSummarizer,
        title_rules: TitleRules | None = None,
        concurrency: int = 1,
    ) -> None:
        self._concurrency = max(1, concurrency)
        # 지금 AI로 보내 답을 기다리는 메일 참조 ID → 업무 ID
        self._in_flight: dict[int, int] = {}
        self._flight_lock = threading.Lock()
        self._rules = title_rules or TitleRules()
        self._tasks = tasks
        self._refs = refs
        self._mail = mail
        self._ai = ai

    def _scan(self) -> tuple[list[tuple[Task, MailRef]], list[tuple[Task, MailRef]]]:
        """(요약하지 않은 첫 메일, 요약하지 않은 받은 후속 메일). 무시한 업무는 제외."""
        firsts: list[tuple[Task, MailRef]] = []
        followups: list[tuple[Task, MailRef]] = []
        for task in self._tasks.list_tasks(None):
            if task.source is not TaskSource.MAIL or task.status is TaskStatus.DISMISSED:
                continue
            refs = self._refs.list_for_task(task.id) if task.id is not None else []
            if not refs:
                continue
            first = min(refs, key=lambda r: r.received_at)
            if first.summary is None:
                firsts.append((task, first))
            followups.extend(
                (task, ref)
                for ref in refs
                if ref.id != first.id and ref.summary is None and ref.direction == MAIL_IN
            )
        return firsts, followups

    def pending(self) -> list[tuple[Task, MailRef]]:
        """업무 제목 요약을 기다리는 첫 메일(화면의 '요약 대기' 건수 기준)."""
        return self._scan()[0]

    def queue(self) -> list[tuple[Task, MailRef]]:
        """이번 실행의 대기열: 업무 제목(첫 메일)이 남아 있으면 그것만, 다 끝났을 때만 받은
        후속 메일을 요약한다(낮은 우선순위, 결정기록 0028). 진행률·남은 건수가 제목 기준과
        맞게 유지된다."""
        firsts, followups = self._scan()
        return firsts or followups

    def pending_ids(self) -> set[int]:
        """업무 제목 요약을 기다리는 업무(첫 메일 기준)."""
        return {task.id for task, _ in self.pending() if task.id is not None}

    def running_task_ids(self) -> set[int]:
        """지금 AI로 보내는 중인 업무(요약 중 표시용, 메모리 상태)."""
        with self._flight_lock:
            return set(self._in_flight.values())

    def _is_first(self, task: Task, ref: MailRef) -> bool:
        if task.id is None:
            return True
        refs = self._refs.list_for_task(task.id)
        return not refs or min(refs, key=lambda r: r.received_at).id == ref.id

    def _followup(self, task: Task, first: MailRef) -> str:
        """같은 업무의 가장 최근 받은 후속 메일 발췌(추가·변경 요청 반영). 없으면 빈 문자열."""
        if task.id is None:
            return ""
        for ref in sorted(self._refs.list_for_task(task.id), key=lambda r: r.received_at)[::-1]:
            if ref.id is None or ref.id == first.id:
                continue
            view = self._mail.view(ref.id)
            if view is not None and view.message is not None and not view.message.sent_by_me:
                return mail_excerpt(view.message.body, MAX_FOLLOWUP_EXCERPT)
        return ""

    def _item(self, task: Task, ref: MailRef) -> MaskedText | None:
        """원본을 다시 읽어 요약 입력을 만든다. 원본이 없으면 None."""
        if ref.id is None:
            return None
        view = self._mail.view(ref.id)
        if view is None or view.message is None:
            return None
        message = view.message
        if not self._is_first(task, ref):
            # 후속 메일: 그 메일 하나만 요약한다(업무 제목은 바꾸지 않음)
            text = (
                f"제목: {self._tasks.title_of(message.subject)}\n"
                f"본문:\n{mail_excerpt(message.body, MAX_BODY_EXCERPT)}"
            )
            return mask(mask(text).value[:MAX_ITEM_CHARS])
        parts = [f"제목: {self._tasks.title_of(message.subject)}"]
        if message.attachment_names:
            parts.append("첨부: " + ", ".join(message.attachment_names[:5]))
        parts.append("본문:\n" + mail_excerpt(message.body, MAX_BODY_EXCERPT))
        followup = self._followup(task, ref)
        if followup:
            parts.append("후속 메일:\n" + followup)
        # 마스킹한 뒤 자르고 다시 마스킹 타입으로(경계에서 잘린 값 노출 방지)
        return mask(mask("\n".join(parts)).value[:MAX_ITEM_CHARS])

    def _retry_single(
        self, batch: list[tuple[Task, MailRef, MaskedText]]
    ) -> list[RequestDigest | None]:
        """묶음 답변을 해석하지 못하면 1건씩 다시 보낸다(1건이 형식을 깨도 나머지는 살림)."""
        if len(batch) == 1:
            return [None]
        answers: list[RequestDigest | None] = []
        for _, _, item in batch:
            try:
                answers.extend(self._ai.summarize_requests([item])[:1] or [None])
            except AiParseError:
                answers.append(None)
        return answers

    def _ask_many(
        self, batches: list[list[MaskedText]]
    ) -> list[list[RequestDigest | None] | AiUnavailableError]:
        """묶음들을 동시에 보낸다. 동시 처리를 모르는 AI(`summarize_requests_many` 없음)는 차례로
        보내고, 사용 불가 오류가 나면 남은 묶음은 보내지 않는다."""
        many = getattr(self._ai, "summarize_requests_many", None)
        if many is not None:
            return list(many(batches))
        out: list[list[RequestDigest | None] | AiUnavailableError] = []
        for batch in batches:
            if (
                out
                and isinstance(out[-1], AiUnavailableError)
                and not isinstance(out[-1], AiParseError)
            ):
                out.append(out[-1])
                continue
            try:
                out.append(self._ai.summarize_requests(batch))
            except AiUnavailableError as exc:
                out.append(exc)
        return out

    def _claim(self, ref_id: int, task_id: int | None = None) -> bool:
        with self._flight_lock:
            if ref_id in self._in_flight:
                return False
            self._in_flight[ref_id] = task_id if task_id is not None else -1
            return True

    def _release(self, ref_ids: list[int]) -> None:
        with self._flight_lock:
            for ref_id in ref_ids:
                self._in_flight.pop(ref_id, None)

    def _give_up_or_retry(self, task: Task, ref: MailRef, limit: int) -> bool:
        """실패 1회를 세고, 상한에 닿았으면 메일 제목을 유지하고 끝낸다(True)."""
        assert ref.id is not None  # noqa: S101 - 호출자가 확인
        if self._refs.add_summary_try(ref.id) >= limit:
            self._refs.set_summary(ref.id, task.title[:500], SUMMARY_KEPT)
            return True
        return False

    def _next_batch(
        self, queue: list[tuple[Task, MailRef]]
    ) -> tuple[list[tuple[Task, MailRef, MaskedText]], int]:
        """대기열에서 한 묶음을 만든다. (묶음, 원본이 없어 바로 끝낸 건수)."""
        batch: list[tuple[Task, MailRef, MaskedText]] = []
        skipped = 0
        while queue and len(batch) < BATCH_SIZE:
            task, ref = queue.pop(0)
            if ref.id is None or not self._claim(ref.id, task.id):
                continue  # 이미 보낸 항목은 다시 보내지 않는다
            item = self._item(task, ref)
            if item is None:
                # 원본이 없으면(삭제·이동) 지금 제목을 요약으로 기록하고 다시 시도하지 않는다
                self._refs.set_summary(ref.id, task.title[:500], SUMMARY_MISSING)
                self._release([ref.id])
                skipped += 1
                continue
            batch.append((task, ref, item))
        return batch, skipped

    def _apply(
        self, batch: list[tuple[Task, MailRef, MaskedText]], answers: list[RequestDigest | None]
    ) -> tuple[int, int, int]:
        """답변을 저장한다. (성공, 실패, 제목 유지) 건수."""
        done = failed = kept = 0
        for (task, ref, _), digest in zip(batch, answers, strict=False):
            if task.id is None or ref.id is None:
                continue
            if digest is None:
                failed += 1
                kept += self._give_up_or_retry(task, ref, MAX_SUMMARY_TRIES)
                continue
            if self._rules.is_generic(digest.title):
                # 일반 문구뿐이면 한 번 더 요청하고, 그래도 같으면 메일 제목 정리본을 유지
                if self._give_up_or_retry(task, ref, MAX_GENERIC_RETRIES + 1):
                    self._refs.set_digest(ref.id, mask(digest.render()).value)
                    kept += 1
                continue
            if self._is_first(task, ref):
                self._tasks.set_summary_title(task.id, digest.title)
            self._refs.set_digest(ref.id, mask(digest.render()).value)
            self._refs.set_summary(ref.id, mask(digest.title).value[:500], SUMMARY_DONE)
            done += 1
        return done, failed, kept

    def run(self, max_batches: int, progress: ProgressCallback | None = None) -> SummaryRun:
        queue = self.queue()
        total, handled = len(queue), 0
        done = failed = kept = 0
        notify = progress or (lambda _total, _handled: None)
        notify(total, 0)
        held: list[int] = []  # 이번 실행이 잡은 항목(예외가 나도 반드시 풀어 준다)
        try:
            batches_left = max_batches
            while batches_left > 0 and queue:
                wave: list[list[tuple[Task, MailRef, MaskedText]]] = []
                while queue and len(wave) < min(self._concurrency, batches_left):
                    batch, skipped = self._next_batch(queue)
                    handled += skipped
                    if batch:
                        wave.append(batch)
                        held.extend(r.id for _, r, _ in batch if r.id is not None)
                if not wave:
                    notify(total, handled)
                    break
                batches_left -= len(wave)
                claimed = [ref.id for batch in wave for _, ref, _ in batch if ref.id is not None]
                try:
                    outcomes = self._ask_many([[item for _, _, item in batch] for batch in wave])
                except AiUnavailableError as exc:
                    left = len(queue) + len(claimed)
                    self._release(claimed)
                    return SummaryRun(done, failed, left, str(exc), kept)
                stop: AiUnavailableError | None = None
                stopped_items = 0
                for batch, outcome in zip(wave, outcomes, strict=True):
                    if isinstance(outcome, AiParseError):
                        try:
                            answers = self._retry_single(batch)
                        except AiUnavailableError as exc:
                            outcome = exc
                        else:
                            outcome = answers
                    if isinstance(outcome, AiUnavailableError):
                        stop, stopped_items = stop or outcome, stopped_items + len(batch)
                        continue
                    d, f, k = self._apply(batch, outcome)
                    done, failed, kept, handled = (
                        done + d,
                        failed + f,
                        kept + k,
                        handled + len(batch),
                    )
                    notify(total, handled)
                self._release(claimed)
                if stop is not None:
                    return SummaryRun(done, failed, len(queue) + stopped_items, str(stop), kept)
            notify(total, handled)
            return SummaryRun(done, failed, len(self.pending()), kept=kept)
        finally:
            self._release(held)
