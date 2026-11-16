"""G-Knowledge 등록·검색 서비스(GK-01·02·05·06, SRCH-02).

- 미리보기 → 사용자 확인 → 등록 순서만 허용한다.
- 중복 등록은 허용하지 않는다(GK-06, 결정기록 0008 G-6). 이미 등록한 업무와 같은 업무의 동시 등록은
  거부한다. G-Knowledge 같은 제목 검사는 보조 수단이다. 검색이 설정·동작할 때만 하고,
  검색이 실패하면 건너뛴다(차단을 보장하지 않음).
- 등록 결과 링크·시각을 업무에 저장하고 이벤트(GK 등록)·지표 원장을 갱신한다.
"""

from __future__ import annotations

import logging
import re
import threading

from domain.knowledge import (
    DuplicateRegistrationError,
    KnowledgeHit,
    KnowledgeStore,
    KnowledgeUnavailableError,
    MaskedDraft,
    StepCallback,
    build_draft,
)
from domain.models import Task
from domain.ports import HistoryRepository
from domain.tasks.service import TaskService
from security.masking import MaskedText

logger = logging.getLogger(__name__)
ALREADY_REGISTERED = "이미 G-Knowledge에 등록한 업무입니다(중복 등록 불가)"
_SPACES = re.compile(r"\s+")


def _norm_title(title: str) -> str:
    return _SPACES.sub(" ", title).strip().casefold()


class KnowledgeService:
    def __init__(
        self,
        store: KnowledgeStore,
        tasks: TaskService,
        history: HistoryRepository,
        assignee: str,
        home_link: str = "",
    ) -> None:
        self.store = store
        self._home = home_link  # G-Knowledge 첫 화면(수동 확인 표시 때 남길 링크)
        self._tasks = tasks
        self._history = history
        self._assignee = assignee
        self._lock = threading.Lock()
        self._in_progress: set[int] = set()

    def preview(self, task_id: int) -> MaskedDraft:
        task = self._tasks.get(task_id)
        return build_draft(task, self._history.get_for_task(task_id), self._assignee, None)

    def check_can_register(self, task_id: int) -> Task:
        """큐에 넣기 전 확인(결정기록 0035): 없는 업무·이미 등록·등록 중이면 예외."""
        task = self._tasks.get(task_id)
        if task.gk_registered:
            raise DuplicateRegistrationError(ALREADY_REGISTERED)
        with self._lock:
            if task_id in self._in_progress:
                raise DuplicateRegistrationError("같은 업무를 등록하는 중입니다")
        return task

    def mark_registered_manually(self, task_id: int) -> Task:
        """사용자가 G-Knowledge에서 직접 확인한 뒤 '등록됨으로 표시'(결과 미확인 건만, 0035)."""
        if self._tasks.get(task_id).gk_registered:
            raise DuplicateRegistrationError(ALREADY_REGISTERED)
        return self._tasks.mark_gk_registered(task_id, self._home)

    def register(
        self, task_id: int, draft: MaskedDraft, on_step: StepCallback | None = None
    ) -> Task:
        if self._tasks.get(task_id).gk_registered:
            raise DuplicateRegistrationError(ALREADY_REGISTERED)
        with self._lock:
            if task_id in self._in_progress:
                raise DuplicateRegistrationError("같은 업무를 등록하는 중입니다")
            self._in_progress.add(task_id)
        try:
            # 진행 중 표시를 한 뒤 다시 확인한다(먼저 끝난 요청과 겹치는 경쟁 조건 방지)
            if self._tasks.get(task_id).gk_registered:
                raise DuplicateRegistrationError(ALREADY_REGISTERED)
            self._reject_same_title(draft)
            # 단계 콜백이 없으면 예전 호출 형태 그대로(콜백을 모르는 저장소 대역과 호환)
            result = self.store.register(draft, on_step) if on_step else self.store.register(draft)
            try:
                return self._tasks.mark_gk_registered(task_id, result.link)
            except Exception:
                # 외부 등록 후 로컬 기록만 실패. 재시도하면 중복이 생기므로 수동 확인 필요
                logger.error("G-Knowledge 등록 후 로컬 기록 실패(업무 %s): 수동 확인 필요", task_id)
                raise
        finally:
            with self._lock:
                self._in_progress.discard(task_id)

    def _reject_same_title(self, draft: MaskedDraft) -> None:
        """G-Knowledge에 같은 제목(공백·대소문자 무시)이 있으면 거부한다. 검색 실패 시 건너뜀."""
        try:
            hits = self.store.search(draft.title)
        except KnowledgeUnavailableError:
            return
        wanted = _norm_title(draft.title.value)
        if any(_norm_title(hit.title) == wanted for hit in hits):
            raise DuplicateRegistrationError("G-Knowledge에 같은 제목의 지식이 이미 있습니다")

    def search(self, query: MaskedText) -> list[KnowledgeHit]:
        return self.store.search(query)
