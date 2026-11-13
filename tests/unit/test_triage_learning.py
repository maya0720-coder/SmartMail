"""'업무 아님' 학습 테스트(TODO-02, 결정기록 0015, 명세 8장 ⑮). 합성 데이터만 쓴다."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from domain.clock import KST, FixedClock
from domain.mail import MailKey, MailMessage, MailThread
from domain.models import MailSource, TaskStatus
from domain.tasks.triage_rules import (
    FLOOR,
    PREFIX,
    SENDER,
    TriageLearning,
    normalize_sender,
    subject_prefix,
)
from fixtures.app_factory import make_container
from security.hashing import LocalKeyHasher
from storage.db import Database
from storage.triage_rules_repo import SqliteTriageRules

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=KST)
SENDER_ADDR = "Notice.Bot@example.invalid"


def _learning(tmp_path: Path) -> TriageLearning:
    db = Database(tmp_path / "t.db")
    return TriageLearning(SqliteTriageRules(db, FixedClock(NOW)), LocalKeyHasher(None))


def test_rule_key_normalization() -> None:
    assert normalize_sender("  Notice.Bot@Example.INVALID ") == "notice.bot@example.invalid"
    assert normalize_sender("   ") is None
    assert subject_prefix("RE: FW: [ 사내  공지 ] 점검 안내") == "사내 공지"
    assert subject_prefix("[외부메일][공지] 안내") == "외부메일"  # 첫 묶음만
    assert subject_prefix("공지 안내") is None
    assert subject_prefix("[] 빈 말머리") is None


# ⑮ 감점: 보낸 사람 −0.15×min(n,3), 말머리 −0.1×min(n,3), 하한 0.05
@pytest.mark.parametrize(
    ("sender_n", "prefix_n", "start", "expected"),
    [
        (0, 0, 0.9, 0.9),
        (1, 0, 0.9, 0.75),
        (2, 1, 0.9, 0.5),
        (5, 0, 0.9, 0.45),  # 3회까지만 센다
        (3, 3, 0.9, 0.15),
        (3, 3, 0.5, FLOOR),  # 하한
    ],
)
def test_todo02_learning_penalty(
    tmp_path: Path, sender_n: int, prefix_n: int, start: float, expected: float
) -> None:
    learning = _learning(tmp_path)
    keys = learning.keys_for(SENDER_ADDR, "[공지] 서버 점검")
    sender_key = next(k for k in keys if k.kind == SENDER)
    prefix_key = next(k for k in keys if k.kind == PREFIX)
    for _ in range(sender_n):
        learning.record((sender_key,), +1)
    for _ in range(prefix_n):
        learning.record((prefix_key,), +1)
    confidence, penalized = learning.adjust(start, keys)
    assert confidence == pytest.approx(expected)
    assert penalized is (sender_n + prefix_n > 0)


def test_todo02_unlearn_decrements_and_removes_rule(tmp_path: Path) -> None:
    learning = _learning(tmp_path)
    keys = learning.keys_for(SENDER_ADDR, "[공지] 안내")
    learning.record(keys, +1)
    assert learning.total() == 2
    learning.record(keys, -1)
    assert learning.total() == 0  # 0회가 된 규칙은 지운다
    learning.record(keys, -1)  # 0 아래로 내려가지 않음
    assert learning.adjust(0.9, keys) == (0.9, False)


def _thread(n: int, subject: str, address: str) -> MailThread:
    message = MailMessage(
        key=MailKey(MailSource.OUTLOOK, f"L{n}", f"<l{n}@x.invalid>"),
        thread_id=f"learn-{n}",
        subject=subject,
        sender_name="합성 발신자(합성팀)",
        received_at=NOW - timedelta(hours=n),
        folder="받은편지함",
        to_me=True,
        sender_address=address,
        body="안녕하세요.\n점검 결과 확인 부탁드립니다. 금주 금요일까지 회신 바랍니다.",
    )
    return MailThread(message.thread_id, (message,))


def test_todo02_learning_lowers_confidence_but_never_excludes(tmp_path: Path) -> None:
    """감점이 커도 후보는 만들어지고(자동 제외 없음) 이유에 learned_penalty가 붙는다."""
    container = make_container(tmp_path, FixedClock(NOW))
    for _ in range(3):
        container.tasks.learn_dismissal(SENDER_ADDR, "[공지] 예전 메일")
    result = container.tasks.ingest(
        [
            _thread(1, "[공지] 점검 결과 확인 요청", SENDER_ADDR),
            _thread(2, "점검 결과 확인 요청", "other@x.invalid"),
        ]
    )
    tasks = {t.thread_id: t for t, _ in result.suggested}
    assert set(tasks) == {"learn-1", "learn-2"}
    learned, plain = tasks["learn-1"], tasks["learn-2"]
    assert learned.status is TaskStatus.SUGGESTED
    assert "learned_penalty" in learned.reasons and "learned_penalty" not in plain.reasons
    assert learned.confidence is not None and plain.confidence is not None
    assert FLOOR <= learned.confidence < plain.confidence
    assert learned.confidence == pytest.approx(max(FLOOR, plain.confidence - 0.45 - 0.3))
