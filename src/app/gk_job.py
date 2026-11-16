"""G-Knowledge 백그라운드 등록 큐(N2, GK-03·04·06, 결정기록 0035).

- 화면 요청은 검증·재마스킹 뒤 큐에 넣고 바로 응답한다(202).
- 브라우저 실행기가 하나라서 한 번에 1건씩 처리한다.
- 상태: queued → running(단계: open·analyze·submit) → requested | failed_unsent | unknown
  - failed_unsent: 등록요청 **전** 실패. 다시 시도해도 중복 등록이 생기지 않는다.
  - unknown: 등록요청 **뒤** 결과 미확인. 자동으로 다시 보내지 않는다(사용자 확인 후 재시도·표시).
- 큐에는 마스킹된 초안만 메모리에 둔다. 앱을 끄면 사라지고, DB에는 상태 값(코드)만 남는다.
- 앱 시작 시 남아 있던 queued·running을 정리한다(등록요청 단계였으면 unknown).
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from domain.knowledge import (
    STEP_SUBMIT,
    DuplicateRegistrationError,
    KnowledgeUnavailableError,
    MaskedDraft,
    RegisterNotSentError,
    RegisterResultUnknownError,
)
from domain.knowledge_service import KnowledgeService
from domain.ports import Clock
from security.logging_policy import EventType, Site, log_event
from storage.gk_state_repo import SqliteGkStates

logger = logging.getLogger(__name__)

QUEUED: Final = "queued"
RUNNING: Final = "running"
REQUESTED: Final = "requested"
FAILED_UNSENT: Final = "failed_unsent"
UNKNOWN: Final = "unknown"
ACTIVE: Final = frozenset({QUEUED, RUNNING})
MAX_RECENT: Final = 50  # 작업 센터에 보여 줄 최근 작업 수(메모리)
STOP_WAIT_SECONDS: Final = 5.0


class RetryConfirmRequiredError(DuplicateRegistrationError):
    """결과 미확인(unknown) 건을 다시 보내려면 중복 경고 확인이 필요하다."""


@dataclass(slots=True)
class GkStatus:
    task_id: int
    state: str
    at: datetime
    step: str | None = None  # running일 때 단계
    reason: str | None = None  # 실패·미확인 사유 코드
    title: str = ""  # 마스킹된 업무 제목(작업 센터 표시용, 메모리)

    def as_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "state": self.state,
            "step": self.step,
            "reason": self.reason,
            "at": self.at.isoformat(),
        }


class GkRegisterQueue:
    def __init__(self, knowledge: KnowledgeService, states: SqliteGkStates, clock: Clock) -> None:
        self._knowledge = knowledge
        self._states = states
        self._clock = clock
        self._lock = threading.Condition()
        self._queue: deque[tuple[int, MaskedDraft]] = deque()
        self._status: dict[int, GkStatus] = {}
        self._recent: deque[int] = deque(maxlen=MAX_RECENT)
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._recover()

    # ---- 시작·종료 ----
    def _recover(self) -> None:
        """앱 시작: 이전 실행에서 끝나지 못한 작업을 정리한다(큐는 메모리라 사라졌다)."""
        now = self._clock.now()
        for row in self._states.all():
            state, reason = row.state, row.reason
            if state == QUEUED or (state == RUNNING and reason != STEP_SUBMIT):
                state, reason = FAILED_UNSENT, "app_restart"
            elif state == RUNNING:  # 등록요청 단계에서 끊겼으면 등록됐을 수 있다
                state, reason = UNKNOWN, "app_restart"
            if (state, reason) != (row.state, row.reason):
                self._states.set(row.task_id, state, now, reason)
            self._status[row.task_id] = GkStatus(row.task_id, state, row.at or now, reason=reason)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._stopping = False
            self._thread = threading.Thread(target=self._loop, name="smartmail-gk", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._lock.notify_all()
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=STOP_WAIT_SECONDS)

    def _loop(self) -> None:
        while True:
            with self._lock:
                while not self._queue and not self._stopping:
                    self._lock.wait()
                if self._stopping:
                    return
            self.process_next()

    # ---- 화면 요청 ----
    def enqueue(self, task_id: int, draft: MaskedDraft, *, retry_unknown: bool = False) -> GkStatus:
        """검증 후 큐에 넣는다. 없는 업무·이미 등록·등록 중·확인 필요(미확인 재시도)는 예외."""
        self._knowledge.check_can_register(task_id)
        with self._lock:
            current = self._status.get(task_id)
            if current is not None and current.state in ACTIVE:
                raise DuplicateRegistrationError("같은 업무를 등록하는 중입니다")
            if current is not None and current.state == UNKNOWN and not retry_unknown:
                raise RetryConfirmRequiredError(
                    "이전 등록 결과를 확인하지 못했습니다. G-Knowledge에서 먼저 확인해 주세요"
                )
            status = self._update(task_id, QUEUED, title=draft.title.value)
            self._queue.append((task_id, draft))
            self._lock.notify_all()
            return status

    def mark_registered(self, task_id: int) -> GkStatus:
        """결과 미확인 건을 사용자가 G-Knowledge에서 확인한 뒤 '등록됨'으로 표시한다."""
        with self._lock:
            current = self._status.get(task_id)
            if current is None or current.state != UNKNOWN:
                raise ValueError("결과를 확인해야 하는 등록 건이 아닙니다")
        self._knowledge.mark_registered_manually(task_id)
        return self._update(task_id, REQUESTED, reason="manual")

    def statuses(self, task_ids: list[int]) -> dict[int, GkStatus]:
        with self._lock:
            return {i: self._status[i] for i in task_ids if i in self._status}

    def status_of(self, task_id: int) -> GkStatus | None:
        with self._lock:
            return self._status.get(task_id)

    def recent(self) -> list[GkStatus]:
        """작업 센터용: 이번 실행에서 다룬 등록 작업(최근 순, 메모리)."""
        with self._lock:
            return [self._status[i] for i in reversed(self._recent) if i in self._status]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._status.values() if s.state in ACTIVE)

    # ---- 처리 ----
    def process_next(self) -> bool:
        """큐에서 1건을 꺼내 등록한다. 꺼낸 것이 없으면 False(테스트는 직접 부른다)."""
        with self._lock:
            if not self._queue:
                return False
            task_id, draft = self._queue.popleft()
        self._run(task_id, draft)
        return True

    def _run(self, task_id: int, draft: MaskedDraft) -> None:
        self._update(task_id, RUNNING, step="open")

        def on_step(name: str) -> None:
            self._update(task_id, RUNNING, step=name)

        try:
            self._knowledge.register(task_id, draft, on_step)
        except RegisterNotSentError as exc:
            self._finish(task_id, FAILED_UNSENT, exc.code)
        except RegisterResultUnknownError:
            self._finish(task_id, UNKNOWN, "result_unknown")
        except DuplicateRegistrationError:
            # 이미 등록됨(다른 경로로 먼저 끝남) 또는 같은 제목 존재: 보내지 않았다
            self._finish(task_id, FAILED_UNSENT, "duplicate")
        except KnowledgeUnavailableError:
            # 단계 구분 없는 안내(예: 같은 제목 검색 단계)는 보내기 전으로 본다
            self._finish(task_id, FAILED_UNSENT, "failed")
        except Exception:
            # 등록 뒤 로컬 기록 실패 등: 등록됐을 수 있으므로 자동 재시도하지 않는다
            logger.error("G-Knowledge 백그라운드 등록 중 오류(업무 %s): 확인 필요", task_id)
            self._finish(task_id, UNKNOWN, "error")
        else:
            self._finish(task_id, REQUESTED, None)

    def _finish(self, task_id: int, state: str, reason: str | None) -> None:
        log_event(EventType.GK_REGISTER, Site.GKNOWLEDGE, success=state == REQUESTED)
        self._update(task_id, state, reason=reason)

    def _update(
        self,
        task_id: int,
        state: str,
        *,
        step: str | None = None,
        reason: str | None = None,
        title: str | None = None,
    ) -> GkStatus:
        now = self._clock.now()
        with self._lock:
            old = self._status.get(task_id)
            status = GkStatus(
                task_id,
                state,
                now,
                step=step,
                reason=reason,
                title=title if title is not None else (old.title if old else ""),
            )
            self._status[task_id] = status
            if task_id in self._recent:
                self._recent.remove(task_id)
            self._recent.append(task_id)
        # running 단계는 사유 칸에 단계 코드를 남긴다(재시작 때 등록요청 단계였는지 판단)
        self._states.set(task_id, state, now, step if state == RUNNING else reason)
        return status
