"""업무 제목 AI 요청 요약 테스트(TODO-01 개정, 결정기록 0014). 가짜 AI·합성 메일만 쓴다."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from domain.ai import AiParseError, RequestDigest
from domain.clock import FixedClock
from domain.tasks.summarize import body_excerpt
from fixtures.app_factory import app_client, make_container
from integrations.ai.gateway import PayloadRejectedError
from integrations.ai.prompts import parse_digests, summarize_prompt
from security.masking import MaskedText, mask


def test_body_excerpt_cuts_quoted_history() -> None:
    body = (
        "안녕하세요.\n전표 확인 부탁드립니다.\n\n"
        "-----Original Message-----\nFrom: 이전 메일\n옛날 내용"
    )
    # 인사말은 빼고, 인용 앞 내용이 짧으면 원래 메일 본문을 붙인다(결정기록 0021)
    assert body_excerpt(body) == "전표 확인 부탁드립니다. [원래 메일] 옛날 내용"
    assert body_excerpt("보낸 사람: 누구\n본문") == "[원래 메일] 본문"


def test_mail_excerpt_forward_signature_and_lines() -> None:
    """전달 메일은 원래 요청을, 서명·면책 문구는 빼고, 번호 목록 줄바꿈은 살린다."""
    from domain.tasks.mail_text import mail_excerpt

    forward = (
        "아래 건 확인 부탁드립니다.\n\n-----Original Message-----\n"
        "From: 가상\nSent: 2026-11-10\nSubject: 권한 요청\n\n"
        "1. KSB1 조회 권한\n2. CK11N 실행 권한\n감사합니다.\n원가팀 가상 드림\n"
        "본 메일은 수신자 외 열람을 금합니다."
    )
    text = mail_excerpt(forward, 1000)
    assert "[원래 메일]\n1. KSB1 조회 권한\n2. CK11N 실행 권한" in text
    assert "드림" not in text and "열람" not in text and "Subject" not in text
    long_head = "가" * 100 + "\n-----Original Message-----\nFrom: x\n옛날"
    assert "[원래 메일]" not in mail_excerpt(long_head, 1000)  # 앞 내용이 충분하면 인용 생략


def test_parse_digests_by_block() -> None:
    text = (
        "[메일 1]\n제목: 전표 역분개 오류 확인\n요청: 기간 마감 오류 원인 확인 / 재처리\n"
        "기한: 11/12까지\n조치: OB52 전기기간 확인\n\n"
        "**[메일 2]**\n제목: PO 출력물 문구 변경\n요청: 결제 조건 문구 변경\n기한: 없음\n"
        "[메일 3]\n요청: 제목 없는 답"
    )
    first, second, third = parse_digests(text, 3)
    assert first is not None and first.title == "전표 역분개 오류 확인"
    assert first.requests == ("기간 마감 오류 원인 확인", "재처리")
    assert first.due == "11/12까지" and first.action == "OB52 전기기간 확인"
    assert second is not None and second.due is None and second.action is None
    assert third is None  # 제목이 없으면 추측하지 않음
    with pytest.raises(AiParseError):
        parse_digests("형식 없는 답변", 2)


@pytest.mark.parametrize(
    "answer",
    [
        "1. [메일 1]\n제목: 전표 역분개 오류 확인\n요청: 원인\n2. [메일 2]\n제목: PO 문구 변경",
        "### 메일 1\n- **제목:** 전표 역분개 오류 확인\n\n### 메일 2\n- **제목:** PO 문구 변경",
        "[메일 1] 제목: 전표 역분개 오류 확인\n요청: 원인 확인\n[메일 2] 제목: PO 문구 변경",
        "메일 1:\n제목: 전표 역분개 오류 확인\n\n메일 2.\n제목: PO 문구 변경",
        "**메일 1**\n**제목**: 전표 역분개 오류 확인\n\n**메일 2**\n**제목**: PO 문구 변경",
    ],
)
def test_ai05_parse_digests_accepts_common_chat_layouts(answer: str) -> None:
    """실제 채팅 답변에 흔한 머리 꼴(번호·제목 같은 줄·콜론·굵게)도 해석한다(결정기록 0026)."""
    first, second = parse_digests(answer, 2)
    assert first is not None and first.title == "전표 역분개 오류 확인"
    assert second is not None and second.title == "PO 문구 변경"


def test_ai05_mail_word_inside_sentence_is_not_a_header() -> None:
    with pytest.raises(AiParseError):
        parse_digests("메일 1건은 요청이 아닙니다.\n제목: 이건 머리 없는 줄", 1)


def test_ai06_answer_shape_hides_content() -> None:
    from integrations.ai.ground_diag import answer_shape

    shape = answer_shape("[메일 1]\n제목: KSB1 권한 부여 010-1234\n기한: 11/12")
    assert "KSB1" not in shape and "권한" not in shape and "010" not in shape
    assert shape.startswith("lines=3 [가가 9]") and "가가: aaa9" in shape


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
        container.summary_job.run_once(max_batches=10)  # 일반 문구 답변은 한 번 더 요청(0021)
        after = {t["id"]: t for t in client.get("/api/tasks").json()}
        assert not any(t["summary_pending"] for t in after.values())
        # 모든 업무에 구조화 요약(요청 사항·기한·필요 조치)이 생긴다(결정기록 0021)
        digests = [client.get(f"/api/tasks/{i}").json()["digest"] for i in after]
        assert all(d and d.startswith("요청 사항:") for d in digests)
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
        gateway.summarize_requests([mask("가")] * 5)  # 4건 초과
    with pytest.raises(PayloadRejectedError):
        gateway.summarize_requests([mask("가" * 1501)])
    with pytest.raises(TypeError):
        gateway.summarize_requests(["마스킹 안 한 글자"])  # type: ignore[list-item]


def test_gateway_masks_ai_output(tmp_path: Path, clock: FixedClock) -> None:
    class Leaky:
        name = "leaky"

        def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
            leak = "홍길동 010-1234-5678 연락 요청"
            return [RequestDigest(leak, (leak,), leak, leak)] * len(items)

    container = make_container(tmp_path, clock)
    container.ai.adapter = Leaky()  # type: ignore[assignment]
    [digest] = container.ai.summarize_requests([mask("본문")])
    assert digest is not None
    assert "010-1234-5678" not in digest.render() + digest.title  # 모든 칸 재마스킹
