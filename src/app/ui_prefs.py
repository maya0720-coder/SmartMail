"""화면 배치 설정(패널 폭·넓게 보기) 읽기·쓰기(결정기록 0016·0033).

브라우저 저장소를 쓰지 않으므로 로컬 DB 설정(app_setting)에 숫자·켬 표시만 저장한다.
개인정보·업무 내용은 담지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final

from storage.repositories import SqliteAppSettings

# 저장 허용 범위(px). 상한은 넓은 모니터 기준이며(결정기록 0033), 화면별 실제 최소·최대는
# 화면 스크립트가 창 크기(비율·맞은편 패널 최소 폭)에 맞춰 다시 제한한다.
MIN_PANEL_WIDTH: Final = 200
MAX_PANEL_WIDTH: Final = 2400

# API 이름 → app_setting 이름
PREF_KEYS: Final = {"detail_w": "ui_detail_w", "triage_list_w": "ui_triage_list_w"}
# 켬/끔 설정(켜면 "1"을 저장하고, 끄면 지운다)
FLAG_KEYS: Final = {"triage_wide": "ui_triage_wide"}
ALL_KEYS: Final = {**PREF_KEYS, **FLAG_KEYS}
_FLAG_ON: Final = "1"


def load_ui_prefs(store: SqliteAppSettings) -> dict[str, int | bool | None]:
    """저장된 폭(없거나 범위를 벗어나면 None = 화면 기본 폭)과 켬/끔 설정(없으면 False)."""
    prefs: dict[str, int | bool | None] = {}
    for key, name in PREF_KEYS.items():
        raw = store.get(name)
        # ASCII 숫자 1~4자리만 받는다(DB를 직접 고친 값으로 화면이 깨지지 않게)
        plain = raw is not None and raw.isascii() and raw.isdigit() and len(raw) <= 4
        value = int(raw) if raw is not None and plain else None
        in_range = value is not None and MIN_PANEL_WIDTH <= value <= MAX_PANEL_WIDTH
        prefs[key] = value if in_range else None
    for key, name in FLAG_KEYS.items():
        prefs[key] = store.get(name) == _FLAG_ON
    return prefs


def save_ui_prefs(store: SqliteAppSettings, values: Mapping[str, int | bool]) -> None:
    for key, value in values.items():
        if key in FLAG_KEYS:
            if not isinstance(value, bool):
                raise ValueError("켬/끔 설정은 참·거짓만 받습니다")
            if value:
                store.set(FLAG_KEYS[key], _FLAG_ON)
            else:
                store.delete(FLAG_KEYS[key])
            continue
        if isinstance(value, bool) or not MIN_PANEL_WIDTH <= value <= MAX_PANEL_WIDTH:
            raise ValueError("패널 폭이 허용 범위를 벗어났습니다")
        store.set(PREF_KEYS[key], str(value))


def reset_ui_prefs(store: SqliteAppSettings, keys: Iterable[str] | None = None) -> None:
    """저장한 화면 배치를 지운다. keys를 주지 않으면 전부 지운다."""
    for key in ALL_KEYS if keys is None else keys:
        store.delete(ALL_KEYS[key])
