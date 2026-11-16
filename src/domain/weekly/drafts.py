"""주간보고 편집 중 내용 보관(결정기록 0030).

- 자동 보관(30초마다 화면이 보냄): 서버 메모리에만, 주차별 1개. 앱을 끄면 사라진다.
- 임시 저장(사용자가 누름): 정리·마스킹한 사본을 로컬 DB에 저장(보관 기간 후 삭제).
- 어느 쪽이든 허용 목록 정리와 마스킹을 거친 HTML만 둔다. 브라우저 저장소는 쓰지 않는다.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol

from domain.calendar import week_start as week_start_of
from domain.errors import UserInputError
from domain.ports import Clock
from domain.weekly.rich import sanitize

MAX_MEMORY_WEEKS: Final = 8


class DraftRepository(Protocol):
    def save(self, week_start: datetime, html: str, saved_at: datetime) -> None: ...

    def get(self, week_start: datetime) -> tuple[str, datetime] | None: ...


@dataclass(frozen=True, slots=True)
class SavedDraft:
    html: str
    saved_at: datetime
    stored: bool  # True: DB 임시 저장본, False: 메모리 자동 보관본


class WeeklyDraftStore:
    def __init__(
        self, repo: DraftRepository | None, clock: Clock, link_bases: Iterable[str] = ()
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._bases = tuple(link_bases)
        self._memory: OrderedDict[datetime, SavedDraft] = OrderedDict()
        self._lock = threading.Lock()

    def _clean(self, week_start: datetime, html: str) -> str:
        if week_start.tzinfo is None or week_start_of(week_start) != week_start:
            raise UserInputError("주 시작 시각이 올바르지 않습니다")
        return sanitize(html, self._bases)

    def autosave(self, week_start: datetime, html: str) -> SavedDraft:
        saved = SavedDraft(self._clean(week_start, html), self._clock.now(), stored=False)
        with self._lock:
            self._memory[week_start] = saved
            self._memory.move_to_end(week_start)
            while len(self._memory) > MAX_MEMORY_WEEKS:
                self._memory.popitem(last=False)
        return saved

    def save(self, week_start: datetime, html: str) -> SavedDraft:
        if self._repo is None:
            raise UserInputError("임시 저장소를 쓸 수 없습니다")
        clean = self._clean(week_start, html)
        now = self._clock.now()
        self._repo.save(week_start, clean, now)
        saved = SavedDraft(clean, now, stored=True)
        with self._lock:
            self._memory[week_start] = saved
        return saved

    def load(self, week_start: datetime) -> SavedDraft | None:
        """더 최근 것을 돌려준다(메모리 자동 보관본 또는 DB 임시 저장본)."""
        with self._lock:
            memory = self._memory.get(week_start)
        stored = None
        if self._repo is not None:
            found = self._repo.get(week_start)
            if found is not None:
                stored = SavedDraft(found[0], found[1], stored=True)
        candidates = [d for d in (memory, stored) if d is not None]
        return max(candidates, key=lambda d: d.saved_at) if candidates else None
