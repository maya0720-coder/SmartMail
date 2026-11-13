"""화면 배치 설정(패널 폭) 읽기·쓰기(결정기록 0016).

브라우저 저장소를 쓰지 않으므로 로컬 DB 설정(app_setting)에 숫자만 저장한다.
개인정보·업무 내용은 담지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from storage.repositories import SqliteAppSettings

# 저장 허용 범위(px). 화면별 실제 최소·최대는 화면 스크립트가 창 크기에 맞춰 다시 제한한다.
MIN_PANEL_WIDTH: Final = 200
MAX_PANEL_WIDTH: Final = 800

# API 이름 → app_setting 이름
PREF_KEYS: Final = {"detail_w": "ui_detail_w", "triage_list_w": "ui_triage_list_w"}


def load_ui_prefs(store: SqliteAppSettings) -> dict[str, int | None]:
    """저장된 폭. 없거나 범위를 벗어난 값은 None(화면 기본 폭 사용)."""
    prefs: dict[str, int | None] = {}
    for key, name in PREF_KEYS.items():
        raw = store.get(name)
        # ASCII 숫자 1~4자리만 받는다(DB를 직접 고친 값으로 화면이 깨지지 않게)
        plain = raw is not None and raw.isascii() and raw.isdigit() and len(raw) <= 4
        value = int(raw) if raw is not None and plain else None
        in_range = value is not None and MIN_PANEL_WIDTH <= value <= MAX_PANEL_WIDTH
        prefs[key] = value if in_range else None
    return prefs


def save_ui_prefs(store: SqliteAppSettings, values: dict[str, int]) -> None:
    for key, value in values.items():
        if not MIN_PANEL_WIDTH <= value <= MAX_PANEL_WIDTH:
            raise ValueError("패널 폭이 허용 범위를 벗어났습니다")
        store.set(PREF_KEYS[key], str(value))


def reset_ui_prefs(store: SqliteAppSettings, keys: Iterable[str] | None = None) -> None:
    """저장한 폭을 지운다. keys를 주지 않으면 전부 지운다."""
    for key in PREF_KEYS if keys is None else keys:
        store.delete(PREF_KEYS[key])
