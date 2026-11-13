"""'업무 아님' 학습 개인정보 테스트(결정기록 0015, 명세 8장 ⑭⑯).

- 보낸 사람 주소·제목 말머리 원문은 DB 어디에도 저장하지 않고 키 해시만 남는다.
- 학습 초기화 API와 로컬 데이터 전체 삭제는 규칙과 로컬 키를 함께 지운다.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.container import Container
from domain.clock import KST, FixedClock
from domain.mail import MailMessage
from domain.models import Task, TaskStatus
from domain.tasks.triage_rules import subject_prefix
from fixtures.app_factory import app_client, make_container
from security.hashing import KEY_BYTES, DpapiProtector, KeyFileError, LocalKeyHasher
from storage.db import Database

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=KST)


def _all_text(db: Database) -> str:
    """DB 모든 표의 모든 값을 한 문자열로(원문 잔존 검사용)."""
    parts: list[str] = []
    for table in db.table_columns():
        for row in db.query(f"SELECT * FROM {table}"):  # noqa: S608 - 표 이름은 sqlite_master 값
            parts.extend(str(v) for v in tuple(row) if v is not None)
    return "\n".join(parts)


def _dismiss_first(client: TestClient, container: Container) -> tuple[Task, MailMessage, str]:
    client.post("/api/mail/collect", json={})
    task = container.tasks.list_tasks([TaskStatus.SUGGESTED])[0]
    assert task.id is not None
    ref = sorted(container.mail.refs_for_task(task.id), key=lambda r: r.received_at)[0]
    assert ref.id is not None
    view = container.mail.view(ref.id)
    assert view is not None and view.message is not None
    message = view.message
    response = client.post(f"/api/tasks/{task.id}/dismiss", json={})
    assert response.status_code == 200
    return task, message, str(response.json()["decision_token"])


# ⑭ 학습 규칙은 해시만 저장
def test_triage_rules_store_hash_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    container = make_container(tmp_path, FixedClock(NOW))
    caplog.set_level(logging.DEBUG)
    with app_client(container) as client:
        task, message, token = _dismiss_first(client, container)
        client.post(f"/api/tasks/{task.id}/revert", json={"decision_token": token})
        client.post(f"/api/tasks/{task.id}/dismiss", json={})
        client.request("DELETE", "/api/triage-rules", json={"confirm": True})
        client.post(f"/api/tasks/{task.id}/revert", json={"decision_token": token})
        _dismiss_first(client, container)
    logs = caplog.text.lower()
    assert message.sender_address.lower() not in logs
    prefix = subject_prefix(message.subject)
    if prefix:
        assert prefix not in logs
        assert prefix not in _all_text(container.db).lower()  # 말머리 원문도 남지 않음
    rows = container.db.query("SELECT key_hash, kind, dismiss_count FROM triage_rules")
    assert rows and {r["kind"] for r in rows} <= {"sender", "prefix"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", r["key_hash"]) for r in rows)
    assert all(r["dismiss_count"] == 1 for r in rows)
    dump = _all_text(container.db).lower()
    assert message.sender_address and message.sender_address.lower() not in dump
    local = message.sender_address.split("@")[0].lower()
    assert local not in dump  # 주소 앞부분도 남지 않음


def test_key_hash_depends_on_local_key(tmp_path: Path) -> None:
    first = LocalKeyHasher(tmp_path / "k1.bin")
    second = LocalKeyHasher(tmp_path / "k2.bin")
    assert first.digest("sender", "a@x.invalid") != second.digest("sender", "a@x.invalid")
    assert first.digest("sender", "a") != first.digest("prefix", "a")  # 종류별 분리
    again = LocalKeyHasher(tmp_path / "k1.bin")  # 다시 열면 같은 키
    assert again.digest("sender", "a@x.invalid") == first.digest("sender", "a@x.invalid")
    # 파일에는 키 원본이 아니라 DPAPI로 감싼 값만 있다(같은 Windows 계정만 풀 수 있음)
    blob = (tmp_path / "k1.bin").read_bytes()
    assert len(blob) > KEY_BYTES
    assert len(DpapiProtector().unprotect(blob)) == KEY_BYTES
    assert not list(tmp_path.glob("*.tmp"))  # 임시 파일이 남지 않음


@pytest.mark.parametrize("content", [b"", b"broken", b"x" * KEY_BYTES])
def test_corrupt_key_file_fails_closed_without_blocking_decisions(
    tmp_path: Path, content: bytes
) -> None:
    """손상된 키: 해시는 만들지 않고(빈 키 대체 없음) 업무 아님 결정·되돌리기는 그대로 된다."""
    key_file = tmp_path / "bad.bin"
    key_file.write_bytes(content)
    with pytest.raises(KeyFileError):
        LocalKeyHasher(key_file).digest("sender", "a@x.invalid")
    container = make_container(tmp_path, FixedClock(NOW))
    learning = container.tasks._learning  # noqa: SLF001
    assert learning is not None
    learning._hasher = LocalKeyHasher(key_file)  # noqa: SLF001
    with app_client(container) as client:
        task, _message, token = _dismiss_first(client, container)  # 학습만 생략
        assert container.db.query("SELECT count(*) FROM triage_rules")[0][0] == 0
        assert (
            client.post(f"/api/tasks/{task.id}/revert", json={"decision_token": token}).status_code
            == 200
        )
        collected = client.post("/api/mail/collect", json={})  # 반영도 멈추지 않음
        assert collected.status_code == 200


def test_revert_dismiss_decrements_learning(tmp_path: Path) -> None:
    container = make_container(tmp_path, FixedClock(NOW))
    with app_client(container) as client:
        task, _message, token = _dismiss_first(client, container)
        assert client.get("/api/triage-rules").json()["rules"] >= 1
        reverted = client.post(f"/api/tasks/{task.id}/revert", json={"decision_token": token})
        assert reverted.status_code == 200
        assert client.get("/api/triage-rules").json()["rules"] == 0


# ⑯ 학습 초기화 API·전체 삭제에 포함(키 파일 폐기)
def test_triage_rules_reset_api_and_wipe(tmp_path: Path) -> None:
    container = make_container(tmp_path, FixedClock(NOW))
    key_file = tmp_path / "learn.bin"
    hasher = LocalKeyHasher(key_file)
    learning = container.tasks._learning  # noqa: SLF001 - 테스트에서 키 파일 경로를 바꿔 끼운다
    assert learning is not None
    learning._hasher = hasher  # noqa: SLF001
    with app_client(container) as client:
        _dismiss_first(client, container)
        assert key_file.exists()
        count = client.get("/api/triage-rules").json()["rules"]
        assert count >= 1
        no_csrf = client.request(
            "DELETE",
            "/api/triage-rules",
            json={"confirm": True},
            headers={"X-CSRF-Token": "wrong"},
        )
        assert no_csrf.status_code == 403
        unconfirmed = client.request("DELETE", "/api/triage-rules", json={})
        assert unconfirmed.status_code == 422
        reset = client.request("DELETE", "/api/triage-rules", json={"confirm": True})
        assert reset.json() == {"deleted": count}
        assert not key_file.exists()
        assert client.get("/api/triage-rules").json()["rules"] == 0

        _dismiss_first(client, container)
        assert key_file.exists()
        wiped = client.post("/api/data/wipe", json={"confirm": True})
        assert wiped.status_code == 200
        assert container.db.query("SELECT count(*) FROM triage_rules")[0][0] == 0
        assert not key_file.exists()
