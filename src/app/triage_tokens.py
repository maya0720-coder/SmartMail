"""제안 결정 취소용 일회용 토큰(결정기록 0015, 명세 3-5).

- 결정(할 일로·나중에·업무 아님) 직후의 업무 값(결정 직전 스냅샷)을 메모리에만 보관한다.
- 60초 안에, 같은 토큰으로 한 번만 쓸 수 있다. 앱을 끄면 사라진다(저장하지 않음).
- 토큰은 추측할 수 없는 난수이고, 비교는 상수 시간으로 한다.
"""

from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from domain.models import Task
from domain.ports import Clock
from domain.tasks.triage_rules import RuleKey

TOKEN_TTL: Final = timedelta(seconds=60)
MAX_TOKENS: Final = 200
TOKEN_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{20,64}")


@dataclass(frozen=True, slots=True)
class Decision:
    task_id: int
    decision: str  # approved | snoozed | dismissed
    before: Task  # 결정 직전 값
    issued_at: datetime
    learned: tuple[RuleKey, ...] = ()  # 업무 아님 학습 키 해시(취소 때 1 감소)


class DecisionTokens:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._items: dict[str, Decision] = {}
        self._lock = threading.Lock()

    def issue(self, decision: str, before: Task, learned: tuple[RuleKey, ...] = ()) -> str:
        assert before.id is not None  # noqa: S101
        token = secrets.token_urlsafe(24)
        now = self._clock.now()
        with self._lock:
            self._purge(now)
            while len(self._items) >= MAX_TOKENS:
                self._items.pop(next(iter(self._items)))
            self._items[token] = Decision(before.id, decision, before, now, learned)
        return token

    def take(self, task_id: int, token: str) -> Decision | None:
        """맞는 토큰이면 꺼내서(일회용) 돌려준다. 형식 오류·만료·다른 업무면 None."""
        if not TOKEN_PATTERN.fullmatch(token):
            return None
        now = self._clock.now()
        with self._lock:
            self._purge(now)
            found = next((k for k in self._items if secrets.compare_digest(k, token)), None)
            if found is None or self._items[found].task_id != task_id:
                return None
            return self._items.pop(found)

    def _purge(self, now: datetime) -> None:
        for key in [k for k, v in self._items.items() if now - v.issued_at > TOKEN_TTL]:
            del self._items[key]
