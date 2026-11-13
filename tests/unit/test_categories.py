"""업무 구분(사용자 정의 목록) 테스트(TODO-01 개정, 결정기록 0014). 합성 데이터만 쓴다."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.rules import load_category_rules
from domain.clock import FixedClock
from domain.errors import UserInputError
from domain.models import Task, TaskSource, TaskStatus
from domain.tasks.categories import (
    DEFAULT_CATEGORIES,
    CategoryCatalog,
    CategoryRules,
    validate_categories,
)
from domain.tasks.extract import clean_title
from fixtures.app_factory import app_client, make_container
from storage.db import Database
from storage.repositories import SqliteAppSettings, SqliteTaskRepository

RULES = CategoryRules("전자결재", ("전자결재", "결재 요청"), ("approval@x.invalid",))


def test_todo01_approval_detected_by_subject_or_sender() -> None:
    catalog = CategoryCatalog(_MemoryStore(), RULES)
    assert catalog.categorize("[전자결재] 구매 품의", "kim@x.invalid") == "전자결재"
    assert catalog.categorize("결재 요청드립니다", "kim@x.invalid") == "전자결재"
    assert catalog.categorize("공지", "APPROVAL@x.invalid") == "전자결재"
    assert catalog.categorize("전표 오류 확인", "kim@x.invalid") == "일반업무"


def test_todo01_approval_category_missing_falls_back_to_default() -> None:
    store = _MemoryStore()
    catalog = CategoryCatalog(store, RULES)
    catalog.save(["운영", "개선"])
    assert (
        catalog.categorize("[전자결재] 품의", "") == "운영"
    )  # 전자결재가 목록에 없으면 기본(첫 항목)


@pytest.mark.parametrize(
    "values",
    [[], ["a"] * 2, ["x" * 21], ["<b>"], [f"구분{i}" for i in range(9)], ["  "]],
)
def test_todo01_rejects_bad_category_lists(values: list[str]) -> None:
    with pytest.raises(UserInputError):
        validate_categories(values)


def test_catalog_ignores_tampered_value() -> None:
    store = _MemoryStore()
    store.set("task_categories", "정상\n<script>")
    assert CategoryCatalog(store, RULES).categories() == DEFAULT_CATEGORIES


def test_recategorize_old_module_tags(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    repo = SqliteTaskRepository(container.db)
    old = repo.add(
        Task(
            "전자결재 요청 건", TaskStatus.SUGGESTED, TaskSource.MAIL, clock.now(), module_tag="FI"
        )
    )
    plain = repo.add(
        Task("전표 확인", TaskStatus.SUGGESTED, TaskSource.MAIL, clock.now(), module_tag="MM")
    )
    assert container.tasks.recategorize_unknown() == 2
    assert repo.get(old.id).module_tag == "전자결재"  # type: ignore[arg-type,union-attr]
    assert repo.get(plain.id).module_tag == "일반업무"  # type: ignore[arg-type,union-attr]
    assert container.tasks.recategorize_unknown() == 0  # 이미 목록에 있으면 바꾸지 않음


def test_categories_api(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        assert client.get("/api/task-categories").json()["categories"] == list(DEFAULT_CATEGORIES)
        ok = client.put(
            "/api/task-categories", json={"categories": ["일반업무", "전자결재", "SAP 운영"]}
        )
        assert ok.status_code == 200 and ok.json()["categories"][-1] == "SAP 운영"
        assert (
            client.put("/api/task-categories", json={"categories": ["a", "a"]}).status_code == 400
        )
        assert client.put("/api/task-categories", json={"categories": []}).status_code == 422
        assert (
            client.put("/api/task-categories", json={"categories": ["a"], "x": 1}).status_code
            == 422
        )
        task = client.post("/api/tasks", json={"title": "수동 업무"}).json()
        edited = client.post(f"/api/tasks/{task['id']}/edit", json={"module_tag": "SAP 운영"})
        assert edited.status_code == 200 and edited.json()["module_tag"] == "SAP 운영"


def test_category_store_survives_restart(db: Database) -> None:
    catalog = CategoryCatalog(SqliteAppSettings(db), RULES)
    catalog.save(["운영", "전자결재"])
    assert CategoryCatalog(SqliteAppSettings(db), RULES).categories() == ("운영", "전자결재")


def test_default_rules_file_loads() -> None:
    rules, noise = load_category_rules()
    assert rules.approval_category == "전자결재" and "전자결재" in rules.subject_keywords
    assert "[외부메일]" in noise


def test_clean_title_strips_repeated_prefixes() -> None:
    noise = ("[외부메일]", "[골프존]")
    subject = "[외부메일]Re: [외부메일]Re: RE: [골프존]Notes 검토 요청의 건"
    assert clean_title(subject, noise) == "Notes 검토 요청의 건"


class _MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value
