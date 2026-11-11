"""AI-Ground g-llm 연동 테스트(AI-01~08). 가짜 어댑터만 쓰며 외부로 보내지 않는다."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from mypy import api as mypy_api

from domain.ai import AI_LABEL, AiUnavailableError
from domain.clock import FixedClock
from domain.models import TaskEventType
from fixtures.synthetic import group4, synthetic_card, synthetic_rrn
from integrations.ai.fake import FakeKnowledgeAI
from integrations.ai.gateway import AiGateway, PayloadRejectedError, check_payload
from integrations.ai.ground_gllm import GroundGllmAdapter
from security.logging_policy import Site
from security.masking import mask
from security.sessions import SessionStore
from storage.db import Database
from storage.knowledge_repos import SqliteAiCallLog
from storage.repositories import SqliteEventRecorder

ROOT = Path(__file__).resolve().parents[2]


def _gateway(db: Database, clock: FixedClock, adapter: object, timeout: float = 2.0) -> AiGateway:
    return AiGateway(
        adapter,  # type: ignore[arg-type]
        SqliteAiCallLog(db),
        SqliteEventRecorder(db, clock),
        clock,
        timeout_seconds=timeout,
    )


def test_ai01_suggestion_has_label_and_logs_call(db: Database, clock: FixedClock) -> None:
    fake = FakeKnowledgeAI()
    gateway = _gateway(db, clock, fake)
    answer = gateway.suggest_solution(mask("전표 역분개 오류 처리 방법"), [mask("유사 사례")])
    assert answer.label == AI_LABEL
    assert "[FI]" in answer.text
    [record] = SqliteAiCallLog(db).list_recent()
    assert record.success
    events = SqliteEventRecorder(db, clock).list_events(kinds=[TaskEventType.AI_CALLED])
    assert [e.detail for e in events] == ["success"]


def test_ai02_ai03_payload_masked_and_bounded(db: Database, clock: FixedClock) -> None:
    rrn = synthetic_rrn()
    card = group4(synthetic_card())
    fake = FakeKnowledgeAI()
    gateway = _gateway(db, clock, fake)
    gateway.classify_task(mask(f"주민번호 {rrn} 카드 {card} 확인 요청"))
    sent = fake.received[0].value
    assert rrn not in sent and card not in sent
    with pytest.raises(PayloadRejectedError):
        gateway.classify_task(mask("가" * 2001))  # 메일 원문 전체 수준의 길이 거부
    with pytest.raises(TypeError):
        gateway.classify_task("원문 문자열")  # type: ignore[arg-type]


def test_masking_is_idempotent_for_payload_check() -> None:
    text = f"주민 {synthetic_rrn()} 카드 {group4(synthetic_card())} 연락 010-1234-5678"
    masked = mask(text)
    assert check_payload(masked, 2000) is masked


def test_ai_timeout_fails_safely(db: Database, clock: FixedClock) -> None:
    gateway = _gateway(db, clock, FakeKnowledgeAI(delay_seconds=0.5), timeout=0.05)
    with pytest.raises(AiUnavailableError) as info:
        gateway.suggest_solution(mask("배치 지연"))
    assert "늦어" in str(info.value)
    assert not SqliteAiCallLog(db).list_recent()[0].success
    gateway.close()


def test_ai_error_response_fails_safely(db: Database, clock: FixedClock) -> None:
    gateway = _gateway(db, clock, FakeKnowledgeAI(fail=True))
    with pytest.raises(AiUnavailableError):
        gateway.classify_task(mask("확인 요청"))


def test_unexpected_adapter_error_hidden(db: Database, clock: FixedClock) -> None:
    class Broken:
        name = "broken"

        def classify_task(self, summary: object) -> object:
            raise RuntimeError("내부 경로 C:/secret")

    gateway = _gateway(db, clock, Broken())
    with pytest.raises(AiUnavailableError) as info:
        gateway.classify_task(mask("확인"))
    assert "secret" not in str(info.value)


def test_ai05_gllm_requires_login_then_reports_unconfirmed(db: Database, clock: FixedClock) -> None:
    sessions = SessionStore(clock, timedelta(minutes=30))
    adapter = GroundGllmAdapter("https://ai.example.invalid", sessions)
    with pytest.raises(AiUnavailableError, match="로그인"):
        adapter.classify_task(mask("x"))

    class Native:
        def close(self) -> None:
            return None

    sessions.open(Site.AIGROUND, Native())
    with pytest.raises(AiUnavailableError, match="확인 필요"):
        adapter.suggest_solution(mask("x"), [])


@pytest.mark.slow
def test_ai03_plain_str_rejected_for_knowledge_ai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    target = tmp_path / "snippet.py"
    target.write_text(
        "from domain.ai import KnowledgeAI\n"
        "def call(ai: KnowledgeAI) -> None:\n"
        "    ai.suggest_solution('raw mail body', [])\n",
        encoding="utf-8",
    )
    stdout, _err, status = mypy_api.run(
        [
            "--config-file",
            str(ROOT / "pyproject.toml"),
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(target),
        ]
    )
    assert status != 0 and "arg-type" in stdout
