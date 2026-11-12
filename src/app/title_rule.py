"""화면에서 바꾼 위키 제목 규칙을 저장·적용한다(WIKI-02)."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Final

from domain.errors import UserInputError
from domain.weekly.report import WikiTemplate
from domain.weekly.title_rule import validate_title_rule
from storage.repositories import SqliteAppSettings

logger = logging.getLogger(__name__)

FORMAT_KEY: Final = "wiki_title_format"
OFFSET_KEY: Final = "wiki_title_offset_days"


def apply_saved_rule(template: WikiTemplate, store: SqliteAppSettings) -> WikiTemplate:
    """저장된 규칙이 있으면 적용한다. 저장값이 손상됐으면 무시하고 파일 기본값을 쓴다."""
    fmt, offset = store.get(FORMAT_KEY), store.get(OFFSET_KEY)
    if fmt is None or offset is None:
        return template
    try:
        days = int(offset)
        validate_title_rule(fmt, days)
    except (ValueError, UserInputError):
        logger.warning("저장된 위키 제목 규칙이 올바르지 않아 기본값을 씁니다")
        return template
    return replace(template, page_title_format=fmt, title_date_offset_days=days)


def save_rule(
    template: WikiTemplate, store: SqliteAppSettings, page_title_format: str, offset_days: int
) -> WikiTemplate:
    """검증 후 저장하고, 적용된 새 템플릿을 돌려준다. 잘못된 값이면 UserInputError."""
    validate_title_rule(page_title_format, offset_days)
    store.set(FORMAT_KEY, page_title_format)
    store.set(OFFSET_KEY, str(offset_days))
    return replace(
        template, page_title_format=page_title_format, title_date_offset_days=offset_days
    )
