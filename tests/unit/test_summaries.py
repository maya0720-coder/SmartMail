"""업무 제목 AI 요청 요약 테스트(TODO-01 개정, 결정기록 0014). 가짜 AI·합성 메일만 쓴다."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from domain.ai import AiUnavailableError
from domain.clock import FixedClock
from domain.tasks.summarize import body_excerpt
from fixtures.app_factory import app_client, make_container
from integrations.ai.gateway import PayloadRejectedError
from integrations.ai.prompts import parse_summaries, summarize_prompt
from security.masking import MaskedText, mask


def test_body_excerpt_cuts_quoted_history() -> None:
    body = (
        "안녕하세요.\n전표 확인 부탁드립니다.\n\n"
        "-----Original Message-----\nFrom: 이전 메일\n옛날 내용"
    )
    assert body_excerpt(body) == "안녕하세요. 전표 확인 부탁드립니다."
    assert body_excerpt("보낸 사람: 누구\n본문") == ""


def test_parse_summaries_by_number() -> None:
    text = "1. 전표 역분개 오류 확인 요청\n2) PO 출력물 문구 변경\n- 잡담"
    assert parse_summaries(text, 3) == ["전표 역분개 오류 확인 요청", "PO 출력물 문구 변경", None]
    with pytest.raises(AiUnavailableError):
        parse_summaries("형식 없는 답변", 2)


def test_summarize_prompt_contains_only_masked_items() -> None:
    prompt = summarize_prompt([mask("제목: A\n본문: 010-1234-5678로 연락 주세요")])
    assert "010-1234-5678" not in prompt and "[메일 1]" in prompt


def test_summaries_replace_titles_and_are_not_resent(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)  # 데모: 가짜 AI(외부 전송 없음)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        before = {t["id"]: t for t in client.get("/api/tasks").json()}
        assert before and all(t["summary_pending"] for t in before.values())
        status = client.get("/api/summaries/status").json()
        assert status["ready"] and status["pending"] == len(before)
        container.summary_job.run_once(max_batches=10)
        after = {t["id"]: t for t in client.get("/api/tasks").json()}
        assert not any(t["summary_pending"] for t in after.values())
        changed = [i for i in after if after[i]["title"] != before[i]["title"]]
        assert changed  # 메일 제목 대신 요청 문장
        fake = container.ai.adapter
        sent = len(fake.received)  # type: ignore[attr-defined]
        container.summary_job.run_once(max_batches=10)
        # 이미 요약한 메일은 다시 보내지 않음
        assert len(fake.received) == sent  # type: ignore[attr-defined]
        assert client.get("/api/summaries/status").json()["pending"] == 0


def test_summary_waits_for_ai_ground_login(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock, demo_mode=False)
    with app_client(container) as client:
        assert client.get("/api/summaries/status").json()["ready"] is False
        assert client.post("/api/summaries/run", json={}).status_code == 409
        container.summary_job.run_once()  # 로그인 전에는 AI를 부르지 않는다
        assert container.db.query("SELECT COUNT(*) AS n FROM ai_call_log")[0]["n"] == 0


def test_gateway_rejects_oversized_or_unmasked_batch(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    gateway = container.ai
    with pytest.raises(PayloadRejectedError):
        gateway.summarize_requests([mask("가")] * 7)  # 7건 초과
    with pytest.raises(PayloadRejectedError):
        gateway.summarize_requests([mask("가" * 901)])
    with pytest.raises(TypeError):
        gateway.summarize_requests(["마스킹 안 한 글자"])  # type: ignore[list-item]


def test_gateway_masks_ai_output(tmp_path: Path, clock: FixedClock) -> None:
    class Leaky:
        name = "leaky"

        def summarize_requests(self, items: Sequence[MaskedText]) -> list[str | None]:
            return ["홍길동 010-1234-5678 연락 요청"] * len(items)

    container = make_container(tmp_path, clock)
    container.ai.adapter = Leaky()  # type: ignore[assignment]
    [title] = container.ai.summarize_requests([mask("본문")])
    assert title is not None and "010-1234-5678" not in title
