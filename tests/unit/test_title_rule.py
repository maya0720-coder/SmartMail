"""위키 제목 규칙 테스트(WIKI-02). 화면 입력 검증·저장·적용."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from app.title_rule import FORMAT_KEY, OFFSET_KEY, apply_saved_rule, save_rule
from config.rules import load_wiki_template
from config.settings import ConfigError
from domain.clock import KST, FixedClock
from domain.errors import UserInputError
from domain.weekly.report import WikiTemplate
from domain.weekly.title_rule import render_title, validate_title_rule
from fixtures.app_factory import app_client, make_container
from storage.db import Database
from storage.repositories import SqliteAppSettings

THU = datetime(2026, 11, 12, tzinfo=KST)


def test_wiki02_all_placeholders() -> None:
    assert render_title("W{yyyy}-{mm}-{dd} {yy} {yymmdd} {yyyymmdd}", THU) == (
        "W2026-11-12 26 261112 20261112"
    )


def test_wiki02_offset_moves_date() -> None:
    template = WikiTemplate(page_title_format="ERP_{yymmdd}", title_date_offset_days=1)
    assert template.page_title(THU) == "ERP_261113"
    assert replace(template, title_date_offset_days=6).page_title(THU) == "ERP_261118"


@pytest.mark.parametrize(
    ("fmt", "offset"),
    [
        ("", 0),
        (" ERP_{yymmdd}", 0),  # 앞뒤 공백
        ("ERP_" + "x" * 60 + "{dd}", 0),  # 60자 초과
        ("ERP_{hh}{dd}", 0),  # 지원하지 않는 변수
        ("ERP_{yyyy}", 0),  # 일(日) 변수 없음 → 매주 같은 제목
        ("ERP/<b>{yymmdd}", 0),  # 금지 문자·마크업
        ("ERP_{yymmdd}", 7),  # 보정일 범위 밖
        ("ERP_{yymmdd}", -1),
    ],
)
def test_wiki02_rejects_bad_rule(fmt: str, offset: int) -> None:
    with pytest.raises(UserInputError):
        validate_title_rule(fmt, offset)


def test_wiki02_file_rule_validated(tmp_path: Path) -> None:
    bad = tmp_path / "t.toml"
    bad.write_text('page_title_format = "ERP_{yyyy}"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_wiki_template(bad)
    assert load_wiki_template().title_date_offset_days == 0  # 기본 목요일


def test_wiki02_saved_rule_persists_and_tampered_value_ignored(db: Database) -> None:
    store = SqliteAppSettings(db)
    base = WikiTemplate()
    saved = save_rule(base, store, "IE주간_{yyyymmdd}", 1)
    assert saved.page_title(THU) == "IE주간_20261113"
    assert apply_saved_rule(base, store).page_title(THU) == "IE주간_20261113"
    store.set(FORMAT_KEY, "ERP_{yyyy}")  # 손상된 값
    assert apply_saved_rule(base, store) == base
    store.set(FORMAT_KEY, "ERP_{dd}")
    store.set(OFFSET_KEY, "x")
    assert apply_saved_rule(base, store) == base
    with pytest.raises(ValueError):
        store.set("user_password", "x")  # 허용되지 않은 설정 이름


def test_wiki02_title_rule_api(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        rule = client.get("/api/weekly/title-rule").json()
        assert rule["offset_days"] == 0 and rule["weekday_labels"][0] == "목"
        ok = client.put(
            "/api/weekly/title-rule", json={"page_title_format": "ERP_{yymmdd}", "offset_days": 1}
        )
        assert ok.status_code == 200 and ok.json()["offset_days"] == 1
        bad = client.put(
            "/api/weekly/title-rule", json={"page_title_format": "ERP_{yyyy}", "offset_days": 0}
        )
        assert bad.status_code == 400
        extra = client.put(
            "/api/weekly/title-rule",
            json={"page_title_format": "ERP_{dd}", "offset_days": 0, "x": 1},
        )
        assert extra.status_code == 422
        assert client.get("/api/weekly/title-rule").json()["offset_days"] == 1
