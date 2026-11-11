"""G-Knowledge 등록·검색 서비스(GK-01·02·05·06, SRCH-02).

- 미리보기 → 사용자 확인 → 등록 순서만 허용한다.
- 이미 등록된 업무는 `force`(재등록 버튼) 없이는 거부한다.
- 등록 결과 링크·시각을 업무에 저장하고 이벤트(GK 등록)·지표 원장을 갱신한다.
"""

from __future__ import annotations

from domain.knowledge import (
    DuplicateRegistrationError,
    KnowledgeHit,
    KnowledgeStore,
    MaskedDraft,
    build_draft,
)
from domain.models import Task
from domain.ports import HistoryRepository
from domain.tasks.service import TaskService
from security.masking import MaskedText


class KnowledgeService:
    def __init__(
        self,
        store: KnowledgeStore,
        tasks: TaskService,
        history: HistoryRepository,
        assignee: str,
    ) -> None:
        self.store = store
        self._tasks = tasks
        self._history = history
        self._assignee = assignee

    def preview(self, task_id: int) -> MaskedDraft:
        task = self._tasks.get(task_id)
        return build_draft(task, self._history.get_for_task(task_id), self._assignee, None)

    def register(self, task_id: int, draft: MaskedDraft, *, force: bool = False) -> Task:
        task = self._tasks.get(task_id)
        if task.gk_registered and not force:
            raise DuplicateRegistrationError("이미 G-Knowledge에 등록한 업무입니다")
        result = self.store.register(draft)
        return self._tasks.mark_gk_registered(task_id, result.link)

    def search(self, query: MaskedText) -> list[KnowledgeHit]:
        return self.store.search(query)
