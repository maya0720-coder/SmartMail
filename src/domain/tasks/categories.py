"""업무 구분(카테고리) 규칙(TODO-01 개정, 결정기록 0014).

- 구분 목록은 사용자가 정한다(기본: 일반업무, 전자결재). 목록의 첫 항목이 기본 구분이다.
- 메일의 제목·발신자에 전자결재 키워드가 있으면 '전자결재 구분'으로, 아니면 기본 구분으로 둔다.
  키워드·발신자 패턴은 규칙 파일(config/rules/task_categories.toml)에서 관리한다.
- 구분 값은 업무의 `module_tag` 칸(20자)에 저장한다(기존 칸 재사용, 스키마 변경 없음).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from domain.errors import UserInputError

DEFAULT_CATEGORIES: Final = ("일반업무", "전자결재")
MAX_CATEGORIES: Final = 8  # 8 × 20자 + 구분자 7 ≤ app_setting.value 200자
MAX_CATEGORY_CHARS: Final = 20
# 한글·영문·숫자·공백·_-·/()만 허용(화면·위키 출력 안전, 저장 칸 20자)
_CATEGORY_TEXT: Final = re.compile(r"[0-9A-Za-z가-힣 _\-·/()]{1,20}")


@dataclass(frozen=True, slots=True)
class CategoryRules:
    approval_category: str = "전자결재"
    subject_keywords: tuple[str, ...] = ()
    sender_patterns: tuple[str, ...] = ()

    def is_approval(self, subject: str, sender_address: str) -> bool:
        lowered = subject.casefold()
        if any(word.casefold() in lowered for word in self.subject_keywords):
            return True
        sender = sender_address.casefold()
        return any(pattern.casefold() in sender for pattern in self.sender_patterns if pattern)


def validate_categories(values: Sequence[str]) -> tuple[str, ...]:
    """사용자가 정한 구분 목록을 검증한다. 앞뒤 공백 제거, 중복 금지, 1~10개."""
    cleaned = tuple(v.strip() for v in values if v.strip())
    if not 1 <= len(cleaned) <= MAX_CATEGORIES:
        raise UserInputError(f"구분은 1~{MAX_CATEGORIES}개여야 합니다")
    if len(set(cleaned)) != len(cleaned):
        raise UserInputError("같은 구분이 두 번 있습니다")
    for value in cleaned:
        if not _CATEGORY_TEXT.fullmatch(value):
            raise UserInputError(
                f"구분 이름은 {MAX_CATEGORY_CHARS}자 이하의 "
                "한글·영문·숫자·공백·_ - · / ( ) 만 쓸 수 있습니다"
            )
    return cleaned


def categorize(
    subject: str, sender_address: str, categories: Sequence[str], rules: CategoryRules
) -> str:
    """메일에 맞는 구분. 전자결재 구분이 목록에 없으면 기본(첫 항목)으로 둔다."""
    if rules.approval_category in categories and rules.is_approval(subject, sender_address):
        return rules.approval_category
    return categories[0]


class SettingStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...


STORE_KEY: Final = "task_categories"
SEPARATOR: Final = "\n"


class CategoryCatalog:
    """현재 구분 목록(사용자 설정)과 판별 규칙. 저장값이 손상됐으면 기본 목록을 쓴다."""

    def __init__(self, store: SettingStore, rules: CategoryRules) -> None:
        self._store = store
        self.rules = rules

    def categories(self) -> tuple[str, ...]:
        raw = self._store.get(STORE_KEY)
        if raw is None:
            return DEFAULT_CATEGORIES
        try:
            return validate_categories(raw.split(SEPARATOR))
        except UserInputError:
            return DEFAULT_CATEGORIES

    def save(self, values: Sequence[str]) -> tuple[str, ...]:
        cleaned = validate_categories(values)
        self._store.set(STORE_KEY, SEPARATOR.join(cleaned))
        return cleaned

    def categorize(self, subject: str, sender_address: str) -> str:
        return categorize(subject, sender_address, self.categories(), self.rules)

    def allows(self, value: str) -> bool:
        return value in self.categories()

    def is_approval_category(self, value: str) -> bool:
        return value == self.rules.approval_category
