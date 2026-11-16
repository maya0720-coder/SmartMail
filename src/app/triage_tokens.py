"""제안 결정 취소용 일회용 토큰(결정기록 0015, 명세 3-5).

- 결정(할 일로·나중에·업무 아님) 직후의 업무 값(결정 직전 스냅샷)을 메모리에만 보관한다.
- 60초 안에, 같은 토큰으로 한 번만 쓸 수 있다. 앱을 끄면 사라진다(저장하지 않음).
- 토큰은 추측할 수 없는 난수이고, 비교는 상수 시간으로 한다.
- 일괄 결정(결정기록 0036)은 건별 토큰 n개를 묶은 일괄 토큰 1개를 따로 발급한다.
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
MAX_TOKENS: Final = 400  # 일괄 100건 결정 뒤 건별 결정을 이어 해도 일괄 되돌리기가 남게 넉넉히
MAX_BULK_TOKENS: Final = 20
# 한 번에 일괄 처리·체크할 수 있는 최대 건수(서버 검증과 화면 체크 한도가 함께 쓴다, 0040)
MAX_BULK: Final = 100
TOKEN_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{20,64}")


@dataclass(frozen=True, slots=True)
class Decision:
    task_id: int
    decision: str  # approved | snoozed | dismissed
    before: Task  # 결정 직전 값
    issued_at: datetime
    learned: tuple[RuleKey, ...] = ()  # 업무 아님 학습 키 해시(취소 때 1 감소)


@dataclass(frozen=True, slots=True)
class BulkDecision:
    items: tuple[tuple[int, str], ...]  # (업무 번호, 건별 토큰)
    issued_at: datetime


class DecisionTokens:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._items: dict[str, Decision] = {}
        self._bulk: dict[str, BulkDecision] = {}
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

    def issue_bulk(self, items: list[tuple[int, str]]) -> str:
        """건별 토큰 n개를 묶은 일괄 토큰. 건별 토큰과 같은 시간 동안만 쓸 수 있다."""
        token = secrets.token_urlsafe(24)
        now = self._clock.now()
        with self._lock:
            self._purge(now)
            while len(self._bulk) >= MAX_BULK_TOKENS:
                self._bulk.pop(next(iter(self._bulk)))
            self._bulk[token] = BulkDecision(tuple(items), now)
        return token

    def take_bulk(self, token: str) -> BulkDecision | None:
        """맞는 일괄 토큰이면 꺼내서(일회용) 돌려준다. 형식 오류·만료면 None."""
        if not TOKEN_PATTERN.fullmatch(token):
            return None
        now = self._clock.now()
        with self._lock:
            self._purge(now)
            found = next((k for k in self._bulk if secrets.compare_digest(k, token)), None)
            return None if found is None else self._bulk.pop(found)

    def _purge(self, now: datetime) -> None:
        for key in [k for k, v in self._items.items() if now - v.issued_at > TOKEN_TTL]:
            del self._items[key]
        for key in [k for k, b in self._bulk.items() if now - b.issued_at > TOKEN_TTL]:
            del self._bulk[key]
