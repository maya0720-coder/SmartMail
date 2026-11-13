"""업무 제목을 '요청 사항 요약'으로 바꾸는 일괄 처리(TODO-01 개정, 결정기록 0014).

- 메일 제목 대신, 메일 본문에서 요청한 일을 AI(AI-Ground)가 한 줄로 요약한 문장을
  업무 제목으로 쓴다.
- 원문은 저장하지 않는다(MAIL-08). 요약할 때만 원본을 다시 읽어(메모리) 회신 기록·서명을 잘라내고,
  마스킹한 뒤 길이를 제한해 보낸다(AI-02·03). 한 번에 최대 6건을 묶어 호출 횟수를 줄인다.
- 처리한 메일 참조에는 요약을 기록해(mail_ref.summary) 다시 보내지 않는다.
- AI를 쓸 수 없으면(로그인 필요·호출 제한) 멈추고, 다음 실행에서 이어서 한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from domain.ai import AiUnavailableError
from domain.mail_service import MailService
from domain.models import MailRef, Task, TaskSource, TaskStatus
from domain.ports import MailRefRepository
from security.masking import MaskedText, mask

BATCH_SIZE: Final = 6
MAX_ITEM_CHARS: Final = 900
MAX_BODY_EXCERPT: Final = 700
# 회신·전달 기록이 시작되는 줄(그 아래는 이전 메일 인용이라 요약에서 뺀다)
_QUOTE_START: Final = re.compile(
    r"^\s*(?:-{2,}\s*(?:Original Message|원본 메시지|원래 메시지)|From:|보낸 사람:|보낸사람:|"
    r"Sent:|On .{0,80} wrote:|>)",
    re.IGNORECASE,
)


class RequestSummarizer(Protocol):
    def summarize_requests(self, items: Sequence[MaskedText]) -> list[str | None]: ...


class SummaryTasks(Protocol):
    def list_tasks(self, statuses: list[TaskStatus] | None = None) -> list[Task]: ...

    def set_summary_title(self, task_id: int, title: str) -> None: ...

    def title_of(self, subject: str) -> str: ...


@dataclass(frozen=True, slots=True)
class SummaryRun:
    done: int
    failed: int
    remaining: int
    message: str | None = None


def body_excerpt(body: str) -> str:
    """회신 기록 앞까지의 본문을 줄여서 돌려준다."""
    kept: list[str] = []
    for line in body.splitlines():
        if _QUOTE_START.match(line):
            break
        if line.strip():
            kept.append(line.strip())
    return " ".join(kept)[:MAX_BODY_EXCERPT]


class SummaryService:
    def __init__(
        self,
        tasks: SummaryTasks,
        refs: MailRefRepository,
        mail: MailService,
        ai: RequestSummarizer,
    ) -> None:
        self._tasks = tasks
        self._refs = refs
        self._mail = mail
        self._ai = ai

    def _first_ref(self, task: Task) -> MailRef | None:
        if task.id is None:
            return None
        refs = self._refs.list_for_task(task.id)
        return min(refs, key=lambda r: r.received_at) if refs else None

    def pending(self) -> list[tuple[Task, MailRef]]:
        """요약하지 않은 메일 업무(무시한 업무 제외)."""
        result = []
        for task in self._tasks.list_tasks(None):
            if task.source is not TaskSource.MAIL or task.status is TaskStatus.DISMISSED:
                continue
            ref = self._first_ref(task)
            if ref is not None and ref.summary is None:
                result.append((task, ref))
        return result

    def pending_ids(self) -> set[int]:
        return {task.id for task, _ in self.pending() if task.id is not None}

    def _item(self, task: Task, ref: MailRef) -> MaskedText | None:
        """원본을 다시 읽어 요약 입력을 만든다. 원본이 없으면 None."""
        if ref.id is None:
            return None
        view = self._mail.view(ref.id)
        if view is None or view.message is None:
            return None
        message = view.message
        text = f"제목: {self._tasks.title_of(message.subject)}\n본문: {body_excerpt(message.body)}"
        # 마스킹한 뒤 자르고 다시 마스킹 타입으로(경계에서 잘린 값 노출 방지)
        return mask(mask(text).value[:MAX_ITEM_CHARS])

    def run(self, max_batches: int) -> SummaryRun:
        queue = self.pending()
        done = failed = 0
        for _ in range(max_batches):
            batch: list[tuple[Task, MailRef, MaskedText]] = []
            while queue and len(batch) < BATCH_SIZE:
                task, ref = queue.pop(0)
                item = self._item(task, ref)
                if item is None:
                    # 원본이 없으면(삭제·이동) 지금 제목을 요약으로 기록하고 다시 시도하지 않는다
                    if ref.id is not None:
                        self._refs.set_summary(ref.id, task.title[:500])
                    continue
                batch.append((task, ref, item))
            if not batch:
                break
            try:
                answers = self._ai.summarize_requests([item for _, _, item in batch])
            except AiUnavailableError as exc:
                return SummaryRun(done, failed, len(queue) + len(batch), str(exc))
            for (task, ref, _), answer in zip(batch, answers, strict=False):
                if not answer or task.id is None or ref.id is None:
                    failed += 1
                    continue
                self._tasks.set_summary_title(task.id, answer)
                self._refs.set_summary(ref.id, answer[:500])
                done += 1
        return SummaryRun(done, failed, len(self.pending()))
