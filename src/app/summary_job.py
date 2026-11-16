"""업무 제목 AI 요약 작업(결정기록 0014): 주기 실행 + 로그인 직후·화면 요청 시 바로 실행.

- AI-Ground에 로그인돼 있을 때만 실행한다(가짜 AI·데모는 항상).
  로그인 전에는 호출 실패를 쌓지 않는다.
- 한 번 실행하면 남은 묶음을 호출 상한(10분당 20회)에 닿을 때까지 이어서 처리한다.
  상한에 닿거나(어댑터가 TOO_MANY_CALLS를 올림) 남은 것이 없으면 멈추고, 다음 주기에 이어서 한다.
  호출 간격·상한은 어댑터가 지키므로 여기서 2분 간격으로 끊지 않는다(결정기록 0018).
- 같은 시각에 하나만 실행한다. 진행 상태는 메모리에만 둔다.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Final

from domain.tasks.summarize import SummaryService
from security.logging_policy import Site
from security.sessions import SessionStore

logger = logging.getLogger(__name__)

SUMMARY_INTERVAL_SECONDS: Final = 120  # 남은 것을 이어서 처리하려고 다시 깨어나는 주기(2분)
# 한 번 실행에서 이어서 처리할 최대 묶음 수. AI-Ground 호출 상한(10분당 20회)에 맞춰,
# 밀린 요약을 한 번에 연속으로 처리하고 상한에 닿으면 어댑터가 멈춘다(결정기록 0018).
BATCHES_PER_RUN: Final = 20
LOGIN_REQUIRED: Final = "AI-Ground에 로그인하면 요청 요약을 만듭니다."


@dataclass
class SummaryState:
    running: bool = False
    done: int = 0
    failed: int = 0
    # 이번 실행의 진행률(결정기록 0023): 전체 건수와 처리 끝난 건수(성공·제목 유지·실패 처리 포함)
    total: int = 0
    completed: int = 0
    message: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class SummaryJob:
    def __init__(
        self, service: SummaryService, sessions: SessionStore, *, always_ready: bool
    ) -> None:
        self._service = service
        self._sessions = sessions
        self._always_ready = always_ready  # 가짜 AI(데모·테스트)
        self.state = SummaryState()
        # 백그라운드 작업을 끈 앱(테스트)에서는 자동 실행하지 않는다(create_app이 정한다)
        self.auto = True

    def ready(self) -> bool:
        return self._always_ready or self._sessions.current(Site.AIGROUND) is not None

    def _on_progress(self, total: int, completed: int) -> None:
        self.state.total, self.state.completed = total, completed

    def run_once(self, max_batches: int = BATCHES_PER_RUN) -> None:
        if not self.ready():
            self.state.message = LOGIN_REQUIRED
            return
        if not self.state.lock.acquire(blocking=False):
            return  # 이미 실행 중
        self.state.running = True
        try:
            result = self._service.run(max_batches, self._on_progress)
            self.state.done += result.done
            self.state.failed += result.failed
            self.state.message = result.message
        except Exception:
            logger.exception("요청 요약 작업 실패")
            self.state.message = "요청 요약 중 오류가 났습니다. 잠시 후 다시 시도합니다."
        finally:
            self.state.running = False
            self.state.lock.release()

    def kick(self) -> None:
        """화면 요청·로그인 직후: 기다리지 않고 백그라운드에서 한 번 실행한다."""
        if not self.auto:
            return
        threading.Thread(target=self.run_once, name="smartmail-summary", daemon=True).start()

    def pending_ids(self) -> set[int]:
        return self._service.pending_ids()

    def running_ids(self) -> set[int]:
        """지금 AI로 보내는 중인 업무(요약 중 표시, 결정기록 0029)."""
        return self._service.running_task_ids()

    def status(self) -> dict[str, object]:
        total, completed = self.state.total, min(self.state.completed, self.state.total)
        return {
            "total": total,
            "completed": completed,
            "percent": round(completed * 100 / total) if total else 0,
            "pending": len(self._service.pending()),
            "running": self.state.running,
            "done": self.state.done,
            "failed": self.state.failed,
            "ready": self.ready(),
            "message": None
            if self.ready() and self.state.message == LOGIN_REQUIRED
            else self.state.message,
        }
