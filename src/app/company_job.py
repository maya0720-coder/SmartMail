"""외부 회사 도메인 → 회사명 백그라운드 확인(결정기록 0027 D1).

- 수집 흐름을 막지 않도록 요약 작업과 같은 주기로 따로 돈다. 한 번에 몇 개 도메인만 묻는다.
- AI-Ground에는 도메인 문자열 하나만 보낸다. 답은 관문에서 마스킹·형식 검사 후 `source='ai'`로 캐시.
- 매핑·캐시로 이미 찾은 도메인, 개인 메일 도메인(업무에 도메인을 저장하지 않음)은 묻지 않는다.
- 답을 못 받은 도메인은 잠시(메모리) 쉬었다가 다시 묻는다. AI를 쓸 수 없으면 다음 주기로 미룬다.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Final, Protocol

from domain.ai import AiUnavailableError
from domain.models import Task, TaskStatus
from domain.ports import Clock
from domain.tasks.requester import KIND_EXTERNAL, SOURCE_AI, CompanyDirectory, CompanyResolver
from security.logging_policy import Site
from security.masking import MaskedText, mask
from security.sessions import SessionStore

logger = logging.getLogger(__name__)

DOMAINS_PER_RUN: Final = 3
RETRY_AFTER: Final = timedelta(hours=6)


class CompanyNamer(Protocol):
    def company_name(self, domain: MaskedText) -> str | None: ...


class TaskLister(Protocol):
    def list_tasks(self, statuses: list[TaskStatus] | None = None) -> list[Task]: ...


class CompanyJob:
    def __init__(
        self,
        tasks: TaskLister,
        resolver: CompanyResolver,
        cache: CompanyDirectory,
        ai: CompanyNamer,
        sessions: SessionStore,
        clock: Clock,
        *,
        always_ready: bool,
    ) -> None:
        self._tasks = tasks
        self._resolver = resolver
        self._cache = cache
        self._ai = ai
        self._sessions = sessions
        self._clock = clock
        self._always_ready = always_ready
        self._skipped: dict[str, datetime] = {}  # 답을 못 받은 도메인 → 시각(메모리)
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return self._always_ready or self._sessions.current(Site.AIGROUND) is not None

    def unresolved(self) -> list[str]:
        now = self._clock.now()
        domains = {
            task.requester_domain
            for task in self._tasks.list_tasks(None)
            if task.requester_kind == KIND_EXTERNAL and task.requester_domain
        }
        return sorted(
            domain
            for domain in domains
            if self._resolver.needs_ai(domain)
            and now - self._skipped.get(domain, datetime.min.replace(tzinfo=now.tzinfo))
            >= RETRY_AFTER
        )

    def run_once(self, limit: int = DOMAINS_PER_RUN) -> int:
        """도메인 몇 개의 회사명을 묻고 캐시한 건수를 돌려준다."""
        if not self.ready() or not self._lock.acquire(blocking=False):
            return 0
        saved = 0
        try:
            for domain in self.unresolved()[:limit]:
                try:
                    name = self._ai.company_name(mask(domain))
                except AiUnavailableError:
                    break  # 로그인 필요·호출 제한: 다음 주기에
                except Exception as exc:  # 보조 기능 오류가 다음 도메인 처리를 막지 않게
                    logger.warning("회사명 확인 실패: %s", type(exc).__name__)
                    name = None
                if name:
                    self._cache.save(domain, name, SOURCE_AI)
                    saved += 1
                else:
                    self._skipped[domain] = self._clock.now()
        finally:
            self._lock.release()
        return saved
