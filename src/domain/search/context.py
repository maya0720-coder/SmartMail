"""해결 방안 요청에 넣을 업무 맥락 만들기(AI-02, HIST-04, 결정기록 0021).

업무 제목 하나만 보내면 일반론밖에 나오지 않으므로, 다음을 마스킹해 함께 보낸다.
- 업무 제목·구분·요청 부서·기한
- 구조화 요약(요청 사항·필요 조치, 이미 마스킹돼 저장된 값)
- 원본 메일 본문 발췌(메모리에서만 다시 읽음, 서명·인사말 제외) · 첨부 파일명
과거 사례(앱 히스토리 카드·G-Knowledge 결과)도 같은 규칙으로 마스킹한 문장으로 만든다.
"""

from __future__ import annotations

from typing import Final, Protocol

from domain.history_service import HistoryHit
from domain.knowledge import KnowledgeHit
from domain.mail_service import MailService
from domain.models import MailRef, Task
from domain.tasks.mail_text import mail_excerpt
from security.masking import MaskedText, mask

MAX_CONTEXT_CHARS: Final = 1900  # 게이트웨이 상한(2,000자) 안
MAX_EXCERPT: Final = 700
MAX_CASE_CHARS: Final = 900  # 게이트웨이 상한(1,000자) 안


class TaskLookup(Protocol):
    def get(self, task_id: int) -> Task: ...


class RefLookup(Protocol):
    def list_for_task(self, task_id: int) -> list[MailRef]: ...


def _bounded(text: str, limit: int) -> MaskedText:
    # 마스킹한 뒤 자르고 다시 마스킹 타입으로(경계에서 잘린 값 노출 방지)
    return mask(mask(text).value[:limit])


class TaskContextBuilder:
    def __init__(self, tasks: TaskLookup, refs: RefLookup, mail: MailService) -> None:
        self._tasks = tasks
        self._refs = refs
        self._mail = mail

    def build(self, task_id: int) -> MaskedText | None:
        """업무 맥락. 업무가 없으면 None(호출자가 검색어만 쓴다)."""
        try:
            task = self._tasks.get(task_id)
        except LookupError:
            return None
        parts = [f"업무: {task.title}"]
        if task.module_tag:
            parts.append(f"구분: {task.module_tag}")
        if task.requester_dept:
            parts.append(f"요청 부서: {task.requester_dept}")
        if task.due_at:
            parts.append(f"기한: {task.due_at.date().isoformat()}")
        refs = sorted(self._refs.list_for_task(task_id), key=lambda r: r.received_at)
        first = refs[0] if refs else None
        if first is not None and first.digest:
            parts.append(first.digest)
        if first is not None and first.id is not None:
            view = self._mail.view(first.id)
            if view is not None and view.message is not None:
                message = view.message
                if message.attachment_names:
                    parts.append("첨부: " + ", ".join(message.attachment_names[:5]))
                parts.append("메일 발췌:\n" + mail_excerpt(message.body, MAX_EXCERPT))
        return _bounded("\n".join(parts), MAX_CONTEXT_CHARS)


def history_case(hit: HistoryHit) -> MaskedText:
    card = hit.card
    title = hit.task.title if hit.task is not None else card.problem
    text = (
        f"[앱 히스토리] {title} / 문제: {card.problem} / 원인: {card.cause or '-'}"
        f" / 조치: {card.action} / T-code: {card.tcode or '-'}"
    )
    return _bounded(text, MAX_CASE_CHARS)


def knowledge_case(hit: KnowledgeHit) -> MaskedText:
    snippet = f" / 요지: {hit.snippet}" if hit.snippet else ""
    return _bounded(f"[G-Knowledge 문서] {hit.title}{snippet}", MAX_CASE_CHARS)
