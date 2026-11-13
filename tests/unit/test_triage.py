"""제안된 업무 트리아지 1단계 테스트(TODO-01·02, 결정기록 0015). 합성 데이터·가짜 어댑터만 쓴다."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from config.rules import load_title_rules
from domain.calendar import next_business_morning
from domain.clock import KST, FixedClock
from domain.models import Task, TaskSource, TaskStatus
from domain.tasks.service import TransitionError
from domain.tasks.summarize import SummaryService, TitleRules
from fixtures.app_factory import app_client, make_container
from storage.db import SCHEMA, SCHEMA_VERSION, Database
from storage.repositories import SqliteTaskRepository


def _suggested(container, clock: FixedClock, title: str = "합성 제안") -> Task:  # type: ignore[no-untyped-def]
    repo = SqliteTaskRepository(container.db)
    return repo.add(
        Task(title, TaskStatus.SUGGESTED, TaskSource.MAIL, clock.now(), module_tag="일반업무")
    )


# ① 나중에: 다음 근무일 아침까지 숨김(월·금·토 경계)
@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (
            datetime(2026, 11, 16, 10, 0, tzinfo=KST),
            datetime(2026, 11, 17, 8, 0, tzinfo=KST),
        ),  # 월 → 화
        (
            datetime(2026, 11, 19, 23, 59, tzinfo=KST),
            datetime(2026, 11, 20, 8, 0, tzinfo=KST),
        ),  # 목 → 금
        (
            datetime(2026, 11, 20, 9, 0, tzinfo=KST),
            datetime(2026, 11, 23, 8, 0, tzinfo=KST),
        ),  # 금 → 월
        (
            datetime(2026, 11, 21, 12, 0, tzinfo=KST),
            datetime(2026, 11, 23, 8, 0, tzinfo=KST),
        ),  # 토 → 월
        (
            datetime(2026, 11, 22, 7, 0, tzinfo=KST),
            datetime(2026, 11, 23, 8, 0, tzinfo=KST),
        ),  # 일 → 월
    ],
)
def test_todo02_next_business_morning(now: datetime, expected: datetime) -> None:
    assert next_business_morning(now) == expected


def test_todo02_snooze_hides_until_next_business_morning(tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 11, 20, 9, 0, tzinfo=KST))  # 금요일
    container = make_container(tmp_path, clock)
    task = _suggested(container, clock)
    with app_client(container) as client:
        response = client.post(f"/api/tasks/{task.id}/snooze", json={})
        assert response.status_code == 200
        assert response.json()["snooze_until"].startswith("2026-11-23T08:00")  # 월 08:00 KST
        visible = [t["id"] for t in client.get("/api/tasks", params={"status": "suggested"}).json()]
        assert task.id not in visible
        summary = client.get("/api/tasks/suggested/summary").json()
        assert summary["snoozed"] == 1 and summary["visible"] == 0
        clock._current = datetime(
            2026, 11, 23, 8, 0, 1, tzinfo=KST
        )  # 월요일 아침이 지나면 다시 보임
        visible = [t["id"] for t in client.get("/api/tasks", params={"status": "suggested"}).json()]
        assert task.id in visible


# ② 나중에는 제안된 업무만
def test_todo02_snooze_only_for_suggested(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    task = _suggested(container, clock)
    assert task.id is not None
    container.tasks.approve(task.id)
    with pytest.raises(TransitionError):
        container.tasks.snooze(task.id)
    with app_client(container) as client:
        assert client.post(f"/api/tasks/{task.id}/snooze", json={}).status_code == 409


# ③ 결정 취소: 60초 안 이전 값 복원, 만료·토큰 불일치·재사용은 409
def test_todo02_revert_within_60s_restores_previous_fields(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = make_container(tmp_path, clock)
    task = _suggested(container, clock, "원래 제목")
    with app_client(container) as client:
        approved = client.post(
            f"/api/tasks/{task.id}/approve",
            json={
                "title": "고친 제목",
                "module_tag": "전자결재",
                "due_date": "2026-12-01",
                "weekly_section": "planned",
            },
        ).json()
        assert approved["status"] == "waiting" and approved["title"] == "고친 제목"
        token = approved["decision_token"]
        clock.advance(timedelta(seconds=59))
        reverted = client.post(f"/api/tasks/{task.id}/revert", json={"decision_token": token})
        assert reverted.status_code == 200
        body = reverted.json()
        assert body["status"] == "suggested" and body["title"] == "원래 제목"
        assert (
            body["module_tag"] == "일반업무"
            and body["due_at"] is None
            and body["weekly_section"] is None
        )
        # 같은 토큰은 한 번만
        again = client.post(f"/api/tasks/{task.id}/revert", json={"decision_token": token})
        assert again.status_code == 409 and "시간이 지났" in again.json()["error"]
        # 나중에 → 취소
        snoozed = client.post(f"/api/tasks/{task.id}/snooze", json={}).json()
        client.post(
            f"/api/tasks/{task.id}/revert", json={"decision_token": snoozed["decision_token"]}
        )
        assert container.tasks.get(task.id).snooze_until is None  # type: ignore[arg-type]
        # 업무 아님 → 취소
        dismissed = client.post(f"/api/tasks/{task.id}/dismiss", json={}).json()
        back = client.post(
            f"/api/tasks/{task.id}/revert", json={"decision_token": dismissed["decision_token"]}
        ).json()
        assert back["status"] == "suggested" and back["closed_at"] is None


def test_todo02_revert_expired_or_wrong_token_409(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    task = _suggested(container, clock)
    other = _suggested(container, clock, "다른 제안")
    with app_client(container) as client:
        token = client.post(f"/api/tasks/{task.id}/approve", json={}).json()["decision_token"]
        # 다른 업무 번호로는 쓸 수 없다
        assert (
            client.post(f"/api/tasks/{other.id}/revert", json={"decision_token": token}).status_code
            == 409
        )
        clock.advance(timedelta(seconds=61))
        assert (
            client.post(f"/api/tasks/{task.id}/revert", json={"decision_token": token}).status_code
            == 409
        )
        # 형식이 틀린 토큰은 입력 검증에서 거부
        assert (
            client.post(f"/api/tasks/{task.id}/revert", json={"decision_token": "<x>"}).status_code
            == 422
        )
        # 결정 뒤 상태가 더 바뀌면 되돌리지 않는다
        fresh = _suggested(container, clock, "세 번째")
        token2 = client.post(f"/api/tasks/{fresh.id}/approve", json={}).json()["decision_token"]
        client.post(f"/api/tasks/{fresh.id}/status", json={"status": "in_progress"})
        assert (
            client.post(
                f"/api/tasks/{fresh.id}/revert", json={"decision_token": token2}
            ).status_code
            == 409
        )


def test_todo02_revert_does_not_change_transition_table() -> None:
    from domain.tasks.service import ALLOWED_TRANSITIONS

    assert TaskStatus.SUGGESTED not in ALLOWED_TRANSITIONS[TaskStatus.WAITING]
    assert ALLOWED_TRANSITIONS[TaskStatus.DISMISSED] == frozenset()


# ⑥ 일반 문구 요약은 버리고 메일 제목 유지
def test_todo01_generic_summary_falls_back_to_subject(tmp_path: Path, clock: FixedClock) -> None:
    rules = load_title_rules()
    assert rules.is_generic("확인 부탁드립니다.")
    assert rules.is_generic("검토 요청드립니다")
    assert rules.is_generic("문의")  # 8자 미만
    assert not rules.is_generic("KSB1 조회 권한 부여")
    assert not rules.is_generic("PO 결제 조건 문구 변경")

    class Generic:
        def summarize_requests(self, items):  # type: ignore[no-untyped-def]
            return ["확인 부탁드립니다"] * len(items)

    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
    before = {t.id: t.title for t in container.tasks.list_tasks([TaskStatus.SUGGESTED])}
    from storage.repositories import SqliteMailRefRepository

    service = SummaryService(
        container.tasks, SqliteMailRefRepository(container.db), container.mail, Generic(), rules
    )
    result = service.run(max_batches=5)
    assert result.kept == len(before) and result.done == 0
    after = {t.id: t.title for t in container.tasks.list_tasks([TaskStatus.SUGGESTED])}
    assert after == before  # 메일 제목 정리본 유지
    assert service.pending() == []  # 다시 보내지 않음
    assert TitleRules().is_generic("짧음")


# ⑦ 스키마 1 → 2 마이그레이션: 기존 데이터 보존
def test_schema_migration_1_to_2_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)  # 스키마 1 구조(새 열 없음)
    conn.execute(
        "INSERT INTO task (title, status, source, created_at, module_tag) VALUES (?,?,?,?,?)",
        ("기존 업무", "waiting", "mail", "2026-11-01T00:00:00.000000+00:00", "FI"),
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    db = Database(path)
    columns = db.table_columns()
    assert {"snooze_until", "confidence", "reasons", "weekly_section"} <= set(columns["task"])
    assert "triage_rules" in columns
    rows = db.query("SELECT title, module_tag, reasons FROM task")
    assert [(r["title"], r["module_tag"], r["reasons"]) for r in rows] == [
        ("기존 업무", "FI", None)
    ]
    assert db.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION == 2
    db.close()
    again = Database(path)  # 두 번 열어도 안전
    assert len(again.query("SELECT * FROM task")) == 1
    again.close()
