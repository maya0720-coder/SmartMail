"""요약 품질·실패 처리 테스트(TODO-01, AI-02·06, 결정기록 0021). 가짜 AI·합성 메일만 쓴다."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from domain.ai import AiParseError, AiUnavailableError, Classification, RequestDigest
from domain.clock import FixedClock
from domain.mail import MailKey, MailMessage, MailThread
from domain.models import MailSource, TaskStatus
from domain.tasks.extract import AI_UNAVAILABLE_CONFIDENCE, TaskExtractor
from domain.tasks.summarize import MAX_SUMMARY_TRIES, SummaryService
from fixtures.app_factory import app_client, make_container
from security.masking import MaskedText
from storage.repositories import SqliteMailRefRepository


def _service(tmp_path: Path, clock: FixedClock, ai: object) -> tuple[SummaryService, object]:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
    refs = SqliteMailRefRepository(container.db)
    return SummaryService(container.tasks, refs, container.mail, ai), container  # type: ignore[arg-type]


class BatchBreaker:
    """묶음으로 보내면 형식을 깨고, 1건씩 보내면 정상으로 답하는 AI."""

    def __init__(self) -> None:
        self.sizes: list[int] = []

    def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
        self.sizes.append(len(items))
        if len(items) > 1:
            raise AiParseError("형식 오류")
        return [RequestDigest("전표 역분개 오류 원인 확인", ("원인 확인",), "11/12", "OB52 확인")]


def test_batch_parse_failure_retries_one_by_one(tmp_path: Path, clock: FixedClock) -> None:
    ai = BatchBreaker()
    service, container = _service(tmp_path, clock, ai)
    pending = len(service.pending())
    assert pending > 1
    result = service.run(max_batches=10)
    assert result.done == pending and service.pending() == []
    assert any(size > 1 for size in ai.sizes) and ai.sizes.count(1) >= pending
    # 구조화 요약이 마스킹된 텍스트로 저장되고 화면 API로 나간다
    task = container.tasks.list_tasks([TaskStatus.SUGGESTED])[0]  # type: ignore[attr-defined]
    with app_client(container) as client:  # type: ignore[arg-type]
        detail = client.get(f"/api/tasks/{task.id}").json()
    assert "요청 사항:" in detail["digest"] and "필요 조치: OB52 확인" in detail["digest"]


class NeverParses:
    calls = 0

    def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
        self.calls += 1
        return [None] * len(items)


def test_repeated_failures_stop_after_cap(tmp_path: Path, clock: FixedClock) -> None:
    """해석 실패가 상한에 닿으면 메일 제목을 유지하고 다시 보내지 않는다(호출 상한 낭비 방지)."""
    ai = NeverParses()
    service, _ = _service(tmp_path, clock, ai)
    for _ in range(MAX_SUMMARY_TRIES):
        assert service.pending()
        service.run(max_batches=10)
    assert service.pending() == []
    # 제목 요약이 끝나면 받은 후속 메일도 같은 상한으로 처리된다(결정기록 0028 D4)
    for _ in range(MAX_SUMMARY_TRIES):
        service.run(max_batches=10)
    assert service.queue() == []
    calls = ai.calls
    service.run(max_batches=10)
    assert ai.calls == calls


def test_ai_unavailable_stops_and_resumes_later(tmp_path: Path, clock: FixedClock) -> None:
    class Down:
        def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
            raise AiUnavailableError("로그인 필요")

    service, _ = _service(tmp_path, clock, Down())
    before = len(service.pending())
    result = service.run(max_batches=10)
    assert result.message == "로그인 필요" and len(service.pending()) == before  # 횟수를 쓰지 않음


def _ambiguous_thread(clock: FixedClock) -> MailThread:
    message = MailMessage(
        key=MailKey(MailSource.OUTLOOK, "A1", "<a1@x.invalid>"),
        thread_id="TA",
        subject="재고 수불 차이 공유",
        sender_name="자재팀 가상",
        received_at=clock.now(),
        folder="받은 편지함",
        to_me=True,
        body="이번 달 재고 수불 차이 내역 공유드립니다.",
    )
    return MailThread("TA", (message,))


def test_extract_keeps_low_confidence_candidate_when_ai_fails(clock: FixedClock) -> None:
    """애매한 메일에서 AI가 실패하면 버리지 않고 확신도 낮은 제안으로 남긴다."""

    class Failing:
        def classify_task(self, summary: MaskedText, task_id: int | None = None) -> Classification:
            raise AiUnavailableError("호출 제한")

    class SaysNo:
        def classify_task(self, summary: MaskedText, task_id: int | None = None) -> Classification:
            return Classification(False, None, 0.9)

    thread = _ambiguous_thread(clock)
    kept = TaskExtractor(Failing()).extract(thread)
    assert kept is not None and kept.confidence == AI_UNAVAILABLE_CONFIDENCE
    assert "ai_unavailable" in kept.reasons
    assert TaskExtractor(SaysNo()).extract(thread) is None  # AI가 '아님'이면 그대로 제외


def test_summary_eval_key_term_coverage() -> None:
    """핵심어 포함률: T-code·오류 번호·기한이 요약에 남았는지 센다(요약 없으면 0점)."""
    from domain.tasks.summary_eval import evaluate, key_terms

    source = "CK11N 실행 시 메시지 번호 CK 465 오류. 11/20까지 처리 부탁드립니다."
    assert key_terms(source) == {"CK11N", "CK 465", "11/20"}
    result = evaluate([(source, "CK11N 평가 단가 오류\n기한: 11/20까지"), (source, None)])
    assert (result.items, result.expected, result.covered, result.no_digest) == (2, 6, 2, 1)
    assert result.coverage == 0.333
