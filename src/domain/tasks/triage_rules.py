"""'업무 아님' 학습(결정기록 0015, 명세 3-4).

- 업무 아님으로 고른 메일의 보낸 사람 주소와 제목 말머리(첫 `[…]`)를 소문자로 맞춘 뒤
  키 해시로만 바꿔 횟수를 센다. 원문은 저장하지 않는다.
- 새 제안의 확신도를 낮출 뿐 자동으로 빼지 않는다(후보는 항상 만든다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from domain.ports import KeyHasher, TriageRuleStore

SENDER: Final = "sender"
PREFIX: Final = "prefix"
SENDER_STEP: Final = 0.15  # 보낸 사람 일치 1회당 감점
PREFIX_STEP: Final = 0.10  # 말머리 일치 1회당 감점
MAX_COUNTED: Final = 3  # 감점에 세는 최대 횟수
FLOOR: Final = 0.05  # 감점 후 확신도 하한
MAX_KEY_CHARS: Final = 200

_PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd|회신|전달)\s*:\s*)*\[([^\[\]]{1,60})\]", re.IGNORECASE)
_STEPS: Final = {SENDER: SENDER_STEP, PREFIX: PREFIX_STEP}


@dataclass(frozen=True, slots=True)
class RuleKey:
    kind: str  # sender | prefix
    key_hash: str


def normalize_sender(address: str) -> str | None:
    value = address.strip().lower()
    return value[:MAX_KEY_CHARS] if value else None


def subject_prefix(subject: str) -> str | None:
    """제목 첫 `[…]` 말머리(회신·전달 표시는 건너뜀). 없으면 None."""
    found = _PREFIX.match(subject)
    if not found:
        return None
    value = " ".join(found.group(1).split()).lower()
    return value or None


class TriageLearning:
    def __init__(self, store: TriageRuleStore, hasher: KeyHasher) -> None:
        self._store = store
        self._hasher = hasher

    def keys_for(self, sender_address: str, subject: str) -> tuple[RuleKey, ...]:
        """원문을 메모리에서만 써서 키 해시를 만든다."""
        keys: list[RuleKey] = []
        sender = normalize_sender(sender_address)
        if sender:
            keys.append(RuleKey(SENDER, self._hasher.digest(SENDER, sender)))
        prefix = subject_prefix(subject)
        if prefix:
            keys.append(RuleKey(PREFIX, self._hasher.digest(PREFIX, prefix)))
        return tuple(keys)

    def record(self, keys: tuple[RuleKey, ...], delta: int = 1) -> None:
        """업무 아님(+1) 또는 그 취소(−1)."""
        for key in keys:
            self._store.bump(key.kind, key.key_hash, delta)

    def adjust(self, confidence: float, keys: tuple[RuleKey, ...]) -> tuple[float, bool]:
        """학습 감점을 적용한 확신도와 감점 여부. 하한 0.05, 원래 하한보다 낮으면 그대로."""
        if not keys:
            return confidence, False
        counts = self._store.counts([k.key_hash for k in keys])
        penalty = sum(_STEPS[k.kind] * min(counts.get(k.key_hash, 0), MAX_COUNTED) for k in keys)
        if penalty <= 0:
            return confidence, False
        return max(FLOOR, min(confidence, confidence - penalty)), True

    def total(self) -> int:
        return self._store.total()

    def reset(self) -> int:
        """규칙을 모두 지우고 키도 버린다. 지운 규칙 수를 돌려준다."""
        deleted = self._store.clear()
        self._hasher.wipe()
        return deleted
