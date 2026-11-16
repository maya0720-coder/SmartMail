"""요청 요약 병렬 처리·진행률·HTML 전처리·설정 검증 테스트(TODO-01, AI-02·03·06, 결정기록 0023).

가짜 AI·합성 메일만 쓴다. 실제 AI-Ground에는 접속하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from config.settings import MAX_SUMMARY_CONCURRENCY, ConfigError, load_settings
from domain.ai import AiParseError, AiUnavailableError, RequestDigest
from domain.clock import FixedClock
from domain.tasks.mail_text import mail_excerpt, strip_html
from domain.tasks.summarize import BATCH_SIZE
from fixtures.app_factory import FakeBackend, app_client, make_container
from fixtures.chat_fakes import BASE, FakeChatPage, FakeClock
from integrations.ai.fake import FakeKnowledgeAI
from integrations.ai.gateway import PayloadRejectedError
from integrations.ai.ground_browser import (
    MAX_BACKGROUND_CALLS_PER_WINDOW,
    MIN_INTERVAL_SECONDS,
    TOO_MANY_CALLS,
    BrowserGllmAdapter,
    ChatFields,
)
from security.browser import BrowserRuntime
from security.logging_policy import Site
from security.masking import MaskedText, mask
from security.sessions import SessionStore

CHAT = ChatFields("/chat", "", "", "", "#q", "", ".answer", ".stop", "")


class SpyAi:
    """게이트웨이를 감싸 묶음 호출 모양(동시 요청 묶음 수·보낸 항목)을 기록한다."""

    def __init__(self, inner: Any, *, unavailable_from: int | None = None) -> None:
        self._inner = inner
        self.waves: list[int] = []
        self.sent: list[str] = []
        self._unavailable_from = unavailable_from

    def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
        self.sent.extend(i.value for i in items)
        return list(self._inner.summarize_requests(items))

    def summarize_requests_many(
        self, batches: Sequence[Sequence[MaskedText]]
    ) -> list[list[RequestDigest | None] | AiUnavailableError]:
        self.waves.append(len(batches))
        self.sent.extend(i.value for b in batches for i in b)
        out = list(self._inner.summarize_requests_many(batches))
        if self._unavailable_from is not None:
            for index in range(self._unavailable_from, len(out)):
                out[index] = AiUnavailableError("호출 제한")
        return out


def _container(tmp_path: Path, clock: FixedClock, concurrency: int = 3) -> Any:
    return make_container(tmp_path, clock, summary_concurrency=concurrency)


def _spy(container: Any, **kw: Any) -> SpyAi:
    service = container.summary_job._service
    spy = SpyAi(service._ai, **kw)
    service._ai = spy
    return spy


def test_todo01_progress_status_fields_and_callback(tmp_path: Path, clock: FixedClock) -> None:
    container = _container(tmp_path, clock)
    seen: list[tuple[int, int]] = []
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        idle = client.get("/api/summaries/status").json()
        pending = idle["pending"]
        assert pending > 0
        assert idle["total"] == 0 and idle["completed"] == 0 and idle["percent"] == 0
        # 기존 필드는 그대로 둔다(하위 호환)
        assert {"pending", "running", "done", "failed", "ready", "message"} <= set(idle)
        container.summary_job._service.run(10, lambda total, done: seen.append((total, done)))
    assert seen[0] == (pending, 0)  # 시작 알림: 전체 건수
    completed = [c for _, c in seen]
    assert completed == sorted(completed) and all(0 <= c <= t for t, c in seen)
    assert seen[-1][1] == pending  # 모두 처리하면 전체와 같다


def test_todo01_status_percent_after_run(tmp_path: Path, clock: FixedClock) -> None:
    container = _container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        total = client.get("/api/summaries/status").json()["pending"]
        container.summary_job.run_once(10)
        status = client.get("/api/summaries/status").json()
    assert status["total"] == total and status["completed"] == total and status["percent"] == 100


def test_ai06_waves_respect_concurrency_and_resend_nothing(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = _container(tmp_path, clock, concurrency=2)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        service = container.summary_job._service
        spy = _spy(container)
        pending = len(service.pending())
        service.run(20)
        assert spy.waves and max(spy.waves) <= 2  # 동시 묶음 수 상한
        if pending > BATCH_SIZE:
            assert spy.waves[0] == 2  # 여러 묶음이 한 번에 나간다
        assert len(spy.sent) == len(set(spy.sent)) or len(spy.sent) >= pending
        # 한 번의 실행 안에서는 같은 항목을 두 번 보내지 않는다(첫 파동의 항목은 그 실행에서 1회만)
        first_wave = spy.sent[: spy.waves[0] * BATCH_SIZE]
        assert len(first_wave) == len(set(first_wave))
        # 요약이 끝난 항목은 다시 보내지 않는다
        for _ in range(2):
            service.run(20)
        sent = len(spy.sent)
        assert not service.pending()
        service.run(20)
        assert len(spy.sent) == sent


def test_ai06_in_flight_items_are_not_claimed_twice(tmp_path: Path, clock: FixedClock) -> None:
    container = _container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        service = container.summary_job._service
        queue = service.pending()
        ref_id = queue[0][1].id
        assert ref_id is not None and service._claim(ref_id)
        batch, _ = service._next_batch(list(queue))
        assert all(ref.id != ref_id for _, ref, _ in batch)  # 이미 보낸 항목은 건너뜀
        service._release([ref_id] + [r.id for _, r, _ in batch if r.id is not None])
        # 모든 항목을 처리한 뒤 추적 목록은 비어 있다
        service.run(20)
        assert not service._in_flight


def test_ai06_fake_many_runs_batches_concurrently() -> None:
    fake = FakeKnowledgeAI(delay_seconds=0.05)
    batches = [[mask(f"제목: 업무 {n}\n본문:\n전표 확인 부탁드립니다")] for n in range(5)]
    out = fake.summarize_many(batches)
    assert len(out) == 5 and fake.peak_parallel >= 2  # 실제로 겹쳐 진행


def test_ai06_parse_failure_in_one_batch_retries_singly_and_others_proceed(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = _container(tmp_path, clock, concurrency=3)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        service = container.summary_job._service
        inner = service._ai
        singles: list[int] = []

        class FirstParseFails:
            def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
                singles.append(len(items))
                return list(inner.summarize_requests(items))

            def summarize_requests_many(self, batches: Sequence[Sequence[MaskedText]]) -> Any:
                out = list(inner.summarize_requests_many(batches))
                out[0] = AiParseError("형식 오류")  # 첫 묶음만 해석 실패
                return out

        service._ai = FirstParseFails()
        run = service.run(20)
        assert run.message is None
        assert singles and set(singles) == {1}  # 실패한 묶음은 1건씩 다시 보낸다
        assert run.done >= 1  # 나머지 묶음은 정상 저장


def test_ai06_unavailable_stops_and_keeps_pending(tmp_path: Path, clock: FixedClock) -> None:
    container = _container(tmp_path, clock, concurrency=3)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        service = container.summary_job._service
        pending = len(service.pending())
        assert pending > BATCH_SIZE  # 데모 메일은 묶음이 둘 이상 나온다
        spy = _spy(container, unavailable_from=1)
        seen: list[tuple[int, int]] = []
        run = service.run(20, lambda total, done: seen.append((total, done)))
        assert run.message == "호출 제한"
        assert len(spy.waves) == 1  # 중단한 뒤에는 더 보내지 않는다
        assert seen[-1][1] < pending  # 처리하지 못한 건은 완료로 세지 않는다
        assert run.remaining >= 1 and len(service.pending()) >= 1


def test_ai06_whole_unavailable_marks_nothing_done(tmp_path: Path, clock: FixedClock) -> None:
    container = _container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        service = container.summary_job._service
        container.ai.adapter = FakeKnowledgeAI(fail=True)
        before = len(service.pending())
        run = service.run(10)
        assert run.message and run.done == 0
        assert len(service.pending()) == before
        assert not service._in_flight


def test_ai06_gateway_many_checks_payload_masks_output_and_logs_each(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = _container(tmp_path, clock)
    gateway = container.ai
    with pytest.raises(PayloadRejectedError):
        gateway.summarize_requests_many([[mask("가")] * 5])  # 묶음 크기 초과
    with pytest.raises(PayloadRejectedError):
        gateway.summarize_requests_many([[mask("가" * 1501)]])  # 길이 초과
    with pytest.raises(TypeError):
        gateway.summarize_requests_many([["마스킹 안 한 글자"]])

    class Leaky:
        name = "leaky"

        def summarize_many(self, batches: Sequence[Sequence[MaskedText]]) -> Any:
            leak = "홍길동 010-1234-5678 연락 요청"
            return [[RequestDigest(leak, (leak,), leak, leak)] * len(b) for b in batches]

    gateway.adapter = Leaky()
    out = gateway.summarize_requests_many([[mask("본문 하나")], [mask("본문 둘"), mask("본문 셋")]])
    assert [len(batch) for batch in out] == [1, 2]
    digest = out[0][0]
    assert digest is not None and "010-1234-5678" not in digest.render() + digest.title
    assert container.db.query("SELECT COUNT(*) AS n FROM ai_call_log")[0]["n"] == 2  # 묶음마다 기록


# ---- AI-Ground 어댑터: 여러 탭 동시 처리 + 호출 상한 유지 ----
class PageFactory:
    def __init__(self, clock: FakeClock, count: int, chunks: list[str]) -> None:
        self.pages = [FakeChatPage(clock, list(chunks)) for _ in range(count)]
        self._next = 0

    def new_page(self) -> FakeChatPage:
        page = self.pages[self._next]
        self._next += 1
        return page


@pytest.fixture
def runtime() -> Iterator[BrowserRuntime]:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    yield runtime
    runtime.shutdown()


def _multi_adapter(
    clock: FixedClock, runtime: BrowserRuntime, pages: int, chunks: list[str]
) -> tuple[BrowserGllmAdapter, PageFactory, FakeClock]:
    fake_clock = FakeClock()
    factory = PageFactory(fake_clock, pages, chunks)
    context = runtime.open_context()
    context._raw.new_page = factory.new_page  # type: ignore[union-attr]
    sessions = SessionStore(clock, timedelta(minutes=30))
    sessions.open(Site.AIGROUND, context)
    adapter = BrowserGllmAdapter(
        BASE, sessions, CHAT, timeout_seconds=60.0, now=fake_clock.now, sleep=fake_clock.sleep
    )
    return adapter, factory, fake_clock


ANSWER = "[메일 1]\n제목: 전표 역분개 오류 확인\n요청: 원인 확인\n기한: 없음\n조치: OB52 확인"


def _batches(count: int) -> list[list[MaskedText]]:
    return [[mask(f"제목: {n}\n본문: 확인")] for n in range(count)]


def test_ai06_adapter_many_tabs_collect_answers_and_keep_interval(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    adapter, factory, fake_clock = _multi_adapter(clock, runtime, 3, [ANSWER])
    out = adapter.summarize_many(_batches(3))
    assert all(isinstance(r, list) and r[0] is not None for r in out)
    assert all(("close",) in page.log for page in factory.pages)  # 모든 탭을 닫는다
    # 호출 순번은 최소 간격으로 벌어진다(상한 완화 없음)
    times = [t for t, _ in adapter._calls]
    assert len(times) == 3
    assert all(b - a >= MIN_INTERVAL_SECONDS - 1e-9 for a, b in zip(times, times[1:], strict=False))
    assert fake_clock.t >= times[-1]  # 마지막 질문도 자기 순번 이전에 보내지 않았다


def test_ai06_adapter_many_respects_background_cap(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    extra = 3
    count = MAX_BACKGROUND_CALLS_PER_WINDOW + extra
    adapter, _, _ = _multi_adapter(clock, runtime, count, [ANSWER])
    out = adapter.summarize_many(_batches(count))
    sent = [r for r in out if isinstance(r, list)]
    refused = [r for r in out if isinstance(r, AiUnavailableError)]
    assert len(sent) == MAX_BACKGROUND_CALLS_PER_WINDOW  # 백그라운드 몫을 넘기지 않는다
    assert len(refused) == extra and all(str(r) == TOO_MANY_CALLS for r in refused)
    with pytest.raises(AiUnavailableError):  # 몫이 다 찼으면 더 보내지 않는다
        adapter.summarize_many(_batches(1))


def test_ai06_adapter_many_one_tab_timeout_keeps_others(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    adapter, factory, _ = _multi_adapter(clock, runtime, 2, [ANSWER])
    factory.pages[1]._endless = True  # 둘째 탭은 끝나지 않는 스트림
    factory.pages[1]._chunks = []
    out = adapter.summarize_many(_batches(2))
    assert isinstance(out[0], list) and isinstance(out[1], AiUnavailableError)
    assert all(("close",) in page.log for page in factory.pages)


# ---- HTML 전처리 ----
def test_ai02_strip_html_removes_tags_entities_script_and_style() -> None:
    raw = (
        "<html><head><title>제목줄</title><style>.a{color:red}</style></head><body>"
        "<script>alert(1)</script><p>전표&nbsp;확인 &amp; 처리 부탁드립니다.</p>"
        "<div>1. KSB1 조회<br>2. CK11N 실행</div><!-- 숨김 주석 -->"
        "<table><tr><td>A</td><td>B</td></tr></table></body></html>"
    )
    text = strip_html(raw)
    assert "<" not in text and "alert" not in text and "color:red" not in text
    assert "숨김" not in text and "제목줄" not in text
    assert "전표 확인 & 처리 부탁드립니다." in text
    assert "1. KSB1 조회\n2. CK11N 실행" in text
    assert len(text) < len(raw) / 2


def test_ai02_strip_html_keeps_plain_text_and_handles_unclosed_blocks() -> None:
    plain = "금액 a < b 이면 재처리\n다음 줄"
    assert strip_html(plain) == plain  # HTML이 아니면 손대지 않는다
    assert strip_html("<p>앞</p><script>끝나지 않는 코드 <b>x").strip() == "앞"
    assert len(strip_html("<div>" + "가" * 300_000)) <= 200_000  # 입력 상한


def test_ai02_mail_excerpt_strips_html_and_keeps_limit() -> None:
    body = (
        "<p>안녕하세요.</p><p>권한 신청 확인 부탁드립니다. 반영 바랍니다 가나다라마바사.</p>"
        "<p>감사합니다.</p>"
    )
    text = mail_excerpt(body, 1000)
    assert "<" not in text and "안녕하세요" not in text and "감사합니다" not in text
    assert "권한 신청 확인 부탁드립니다" in text
    assert len(mail_excerpt("<p>" + "가" * 2000 + "</p>", 100)) == 100  # 길이 제한 유지


# ---- 설정 ----
@pytest.mark.parametrize("value", ["0", "-1", str(MAX_SUMMARY_CONCURRENCY + 1), "abc"])
def test_summary_concurrency_out_of_range_fails_at_startup(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {
        "SMARTMAIL_APP_PORT": "8765",
        "SMARTMAIL_WIKI_BASE_URL": "https://wiki.example.invalid",
        "SMARTMAIL_GKNOWLEDGE_BASE_URL": "https://gk.example.invalid",
        "SMARTMAIL_AIGROUND_BASE_URL": "https://ai.example.invalid",
        "SMARTMAIL_GROUPWARE_MAIL_BASE_URL": "https://mail.example.invalid",
        "SMARTMAIL_SUMMARY_CONCURRENCY": value,
    }
    for key, item in env.items():
        monkeypatch.setenv(key, item)
    with pytest.raises(ConfigError) as error:
        load_settings(env_file=None)
    assert "SMARTMAIL_SUMMARY_CONCURRENCY" in str(error.value)


def test_summary_concurrency_default_and_valid_range(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "SMARTMAIL_APP_PORT": "8765",
        "SMARTMAIL_WIKI_BASE_URL": "https://wiki.example.invalid",
        "SMARTMAIL_GKNOWLEDGE_BASE_URL": "https://gk.example.invalid",
        "SMARTMAIL_AIGROUND_BASE_URL": "https://ai.example.invalid",
        "SMARTMAIL_GROUPWARE_MAIL_BASE_URL": "https://mail.example.invalid",
    }
    for key, item in env.items():
        monkeypatch.setenv(key, item)
    assert load_settings(env_file=None).summary_concurrency == 5
    monkeypatch.setenv("SMARTMAIL_SUMMARY_CONCURRENCY", str(MAX_SUMMARY_CONCURRENCY))
    assert load_settings(env_file=None).summary_concurrency == MAX_SUMMARY_CONCURRENCY


# ---- 회귀: HTML 전처리 선형 시간(ReDoS) · 예외 시 처리 중 표시 해제 ----
@pytest.mark.parametrize("unit", ["<script ", "<br ", "</td ", "<style>", "<!--", "<a "])
def test_ai02_strip_html_linear_time_on_malicious_input(unit: str) -> None:
    import time

    raw = (unit * (200_000 // len(unit) + 1))[:200_000]
    started = time.perf_counter()
    strip_html(raw)
    assert time.perf_counter() - started < 0.5


def test_ai06_in_flight_released_when_ai_raises_unexpected_error(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = _container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        service = container.summary_job._service
        before = len(service.pending())

        class Boom:
            def summarize_requests_many(self, batches: Sequence[Sequence[MaskedText]]) -> Any:
                raise PayloadRejectedError("검사 실패")

        service._ai = Boom()
        with pytest.raises(PayloadRejectedError):
            service.run(10)
        assert not service._in_flight  # 예외가 나도 '처리 중'으로 남지 않는다
        service._ai = container.ai
        service.run(10)
        assert len(service.pending()) < before  # 다음 실행에서 다시 요약된다


def test_ai06_in_flight_released_when_saving_fails(tmp_path: Path, clock: FixedClock) -> None:
    container = _container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        service = container.summary_job._service

        def broken(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("DB 오류")

        service._refs.set_digest = broken
        with pytest.raises(RuntimeError):
            service.run(10)
        assert not service._in_flight
