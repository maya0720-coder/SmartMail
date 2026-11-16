"""AI-Ground 채팅 화면 자동화 어댑터 테스트(AI-01·04·05, 결정기록 0008).

가짜 브라우저 페이지와 가짜 시계만 쓴다. 실제 AI-Ground에는 접속하지 않는다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from config.settings import ConfigError
from domain.ai import AiUnavailableError
from domain.clock import FixedClock
from fixtures.app_factory import FakeBackend, app_client, make_container
from fixtures.chat_fakes import BASE, FakeChatPage, FakeClock
from integrations.ai.ground_browser import (
    ANSWER_TIMEOUT,
    MAX_CALLS_PER_WINDOW,
    MIN_INTERVAL_SECONDS,
    NOT_SENT,
    BrowserGllmAdapter,
    ChatFields,
    load_chat_fields,
)
from integrations.ai.prompts import parse_answer, parse_classification
from security.browser import BrowserRuntime
from security.logging_policy import Site
from security.masking import mask
from security.sessions import SessionStore

CHAT = ChatFields("/chat", "", "", "", "#q", "", ".answer", ".stop", "")


@pytest.fixture
def runtime() -> Iterator[BrowserRuntime]:
    runtime = BrowserRuntime(backend_factory=FakeBackend)
    yield runtime
    runtime.shutdown()


def _adapter(
    clock: FixedClock,
    runtime: BrowserRuntime,
    chunks: list[str],
    *,
    endless: bool = False,
    chat: ChatFields = CHAT,
    timeout: float = 30.0,
    visible: tuple[str, ...] = ("#q",),
    landing_url: str = BASE + "/chat",
) -> tuple[BrowserGllmAdapter, FakeChatPage, FakeClock]:
    fake_clock = FakeClock()
    page = FakeChatPage(fake_clock, chunks, endless, visible=visible, landing_url=landing_url)
    context = runtime.open_context()
    context._raw.new_page = lambda: page  # type: ignore[union-attr]
    sessions = SessionStore(clock, timedelta(minutes=30))
    sessions.open(Site.AIGROUND, context)
    adapter = BrowserGllmAdapter(
        BASE, sessions, chat, timeout_seconds=timeout, now=fake_clock.now, sleep=fake_clock.sleep
    )
    return adapter, page, fake_clock


def test_ai01_classify_waits_for_streaming_answer(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    adapter, page, _ = _adapter(clock, runtime, ["업무: ", "예\n모듈: fi", "\n확신도: 0.8"])
    result = adapter.classify_task(mask("전표 역분개 요청 / 확인 부탁드립니다"))
    assert result.is_task and result.module_tag == "FI" and result.confidence == 0.8
    assert ("goto", f"{BASE}/chat") in page.log
    [fill] = [entry for entry in page.log if entry[0] == "fill"]
    assert "전표 역분개 요청" in fill[2]
    assert ("press", "#q", "Enter") in page.log and page.log[-1] == ("close",)


def test_ai04_suggestion_text_and_links(clock: FixedClock, runtime: BrowserRuntime) -> None:
    answer = ["1. OB52 기간 확인\n", "2. 재전기\n근거: https://gk.example.invalid/k/1"]
    adapter, _, _ = _adapter(clock, runtime, answer)
    result = adapter.suggest_solution(mask("역분개 오류"), [mask("전기기간 닫힘 사례")])
    assert result.text.startswith("1. OB52")
    assert result.references == ("https://gk.example.invalid/k/1",)


def test_ai06_endless_stream_times_out_and_closes_page(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    adapter, page, _ = _adapter(clock, runtime, ["업무: 예"], endless=True, timeout=5)
    with pytest.raises(AiUnavailableError, match=ANSWER_TIMEOUT):
        adapter.classify_task(mask("요청"))
    assert page.log[-1] == ("close",)


def test_ai05_requires_login_and_confirmed_fields(clock: FixedClock) -> None:
    sessions = SessionStore(clock, timedelta(minutes=30))
    adapter = BrowserGllmAdapter(BASE, sessions, CHAT, timeout_seconds=10)
    with pytest.raises(AiUnavailableError, match="로그인"):
        adapter.classify_task(mask("x"))
    empty = ChatFields("", "", "", "", "", "", "", "", "")
    unready = BrowserGllmAdapter(BASE, sessions, empty, timeout_seconds=10)
    with pytest.raises(AiUnavailableError, match="확인 필요"):
        unready.suggest_solution(mask("x"), [])


def test_ai05_foreign_chat_path_rejected(clock: FixedClock, runtime: BrowserRuntime) -> None:
    chat = ChatFields("https://evil.example/chat", "", "", "", "#q", "", ".answer", "", "")
    adapter, page, _ = _adapter(clock, runtime, ["업무: 예"], chat=chat)
    with pytest.raises(AiUnavailableError, match="주소"):
        adapter.classify_task(mask("요청"))
    assert page.log == []


def test_ai06_throttle_interval_and_window_cap(clock: FixedClock, runtime: BrowserRuntime) -> None:
    adapter, page, fake_clock = _adapter(clock, runtime, [])
    adapter._throttle(False)
    adapter._throttle(False)
    assert fake_clock.sleeps == [MIN_INTERVAL_SECONDS]
    for _ in range(MAX_CALLS_PER_WINDOW - 2):
        adapter._throttle(False)
    with pytest.raises(AiUnavailableError, match="10분"):
        adapter._throttle(False)
    fake_clock.t += 601
    adapter._throttle(False)  # 구간이 지나면 다시 허용
    assert page.log == []


def test_ai06_background_calls_leave_room_for_user_requests(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    """요약·분류(백그라운드)가 밀려도 사용자가 누른 해결 방안 요청 몫은 남는다(결정기록 0021)."""
    from integrations.ai.ground_browser import MAX_BACKGROUND_CALLS_PER_WINDOW

    adapter, _, _ = _adapter(clock, runtime, [])
    for _ in range(MAX_BACKGROUND_CALLS_PER_WINDOW):
        adapter._throttle(True)
    with pytest.raises(AiUnavailableError, match="10분"):
        adapter._throttle(True)
    for _ in range(MAX_CALLS_PER_WINDOW - MAX_BACKGROUND_CALLS_PER_WINDOW):
        adapter._throttle(False)  # 사용자 요청은 남은 몫까지 허용
    with pytest.raises(AiUnavailableError, match="10분"):
        adapter._throttle(False)


@pytest.mark.parametrize("text", ["", "잘 모르겠습니다", "업무 예"])
def test_unparseable_classification_rejected(text: str) -> None:
    with pytest.raises(AiUnavailableError):
        parse_classification(text)


def test_classification_defaults_and_unknown_module() -> None:
    result = parse_classification("**업무**: 아니오\n모듈: 회계")
    assert not result.is_task and result.module_tag is None and result.confidence == 0.5


def test_empty_answer_rejected() -> None:
    with pytest.raises(AiUnavailableError):
        parse_answer("   ")


def test_parsers_linear_on_long_input() -> None:
    started = time.perf_counter()
    parse_answer("https://" + "a" * 50_000)
    with pytest.raises(AiUnavailableError):
        parse_classification(" " * 50_000 + "업무")
    assert time.perf_counter() - started < 0.5


def test_ai01_default_chat_fields_confirmed(tmp_path: Path) -> None:
    # 2026-11-12 실제 화면에서 확인한 선택자(결정기록 0010)
    fields = load_chat_fields()
    assert fields.ready
    assert fields.prompt == "#messageInput"
    assert fields.answer.endswith(".chat-text")  # 복사 버튼 글자가 답변에 섞이지 않게
    empty = tmp_path / "empty.toml"
    empty.write_text("[chat]\n", encoding="utf-8")
    assert not load_chat_fields(empty).ready  # 비어 있으면 호출하지 않음
    bad = tmp_path / "f.toml"
    bad.write_text("[chat]\nunknown = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_chat_fields(bad)


def test_bootstrap_uses_browser_adapter_without_resend(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock, demo_mode=False, ai_adapter="gllm")
    with app_client(container):
        assert isinstance(container.ai.adapter, BrowserGllmAdapter)
        assert container.ai._retries == 0  # 저장·학습되는 곳이므로 재전송하지 않음


def test_ai06_not_sent_when_dequeued_after_deadline(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    """브라우저 스레드가 다른 작업에 잡혀 포기 시각이 지나면, 나중에 꺼내져도 보내지 않는다."""
    adapter, page, fake_clock = _adapter(clock, runtime, ["업무: 예"], timeout=30)
    release = threading.Event()

    def busy() -> None:
        release.wait(5)
        fake_clock.t += 100  # 앞 작업이 포기 시각을 넘길 만큼 오래 걸림

    runtime._executor.submit(busy)
    timer = threading.Timer(0.1, release.set)
    timer.start()
    with pytest.raises(AiUnavailableError, match=NOT_SENT):
        adapter.classify_task(mask("요청"))
    assert not [entry for entry in page.log if entry[0] in ("fill", "press", "click")]


def test_ai06_long_queue_is_refused(clock: FixedClock, runtime: BrowserRuntime) -> None:
    adapter, page, fake_clock = _adapter(clock, runtime, [])
    adapter._calls.append((fake_clock.t + 10, False))  # 앞 호출 예약이 많이 밀려 있음
    with pytest.raises(AiUnavailableError, match=NOT_SENT):
        adapter._throttle(False)
    assert fake_clock.sleeps == [] and page.log == []


def test_runtime_timeout_cancels_queued_job(runtime: BrowserRuntime) -> None:
    release = threading.Event()
    ran: list[str] = []
    runtime._executor.submit(lambda: release.wait(5))
    with pytest.raises(TimeoutError):
        runtime.run(lambda: ran.append("late"), timeout=0.05)
    release.set()
    runtime.run(lambda: None)  # 앞 작업이 다 끝날 때까지 기다림
    assert ran == []


# ---- 결정기록 0026: 실패 단계 진단·숨은 '새 대화' 버튼·SSO 만료 ----
CHAT_NEW = ChatFields("/chat", "#new", "", "", "#q", "#send", ".answer", ".stop", "")


def test_ai01_hidden_new_chat_button_is_skipped(clock: FixedClock, runtime: BrowserRuntime) -> None:
    """대화 목록이 접혀 '새 대화' 버튼이 숨으면 누르지 않고(시간 초과 없이) 첫 화면에서 보낸다."""
    adapter, page, _ = _adapter(clock, runtime, ["업무: 예"], chat=CHAT_NEW)
    result = adapter.classify_task(mask("전표 역분개 요청"))
    assert result.is_task
    assert ("click", "#new") not in page.log
    # 보내기 버튼도 숨어 있으면 입력칸에서 Enter로 보낸다
    assert ("press", "#q", "Enter") in page.log


def test_ai01_visible_new_chat_button_is_clicked(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    adapter, page, _ = _adapter(
        clock, runtime, ["업무: 예"], chat=CHAT_NEW, visible=("#q", "#new", "#send")
    )
    adapter.classify_task(mask("요청"))
    assert ("click", "#new") in page.log and ("click", "#send") in page.log


def test_ai05_sso_redirect_reports_session_expired_with_safe_diagnostics(
    clock: FixedClock, runtime: BrowserRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    """SSO 로그인 화면으로 넘어가면 '로그인 만료'로 안내하고, 로그에는 단계·호스트만."""
    adapter, page, _ = _adapter(
        clock,
        runtime,
        [],
        visible=(),
        landing_url="https://login.example.invalid/oauth2/authorize?client_id=secret-q",
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(AiUnavailableError, match="로그인이 만료"):
            adapter.classify_task(mask("민감한 질문 본문 XYZ"))
    text = caplog.text
    assert "단계=입력칸 대기" in text and "login.example.invalid" in text
    assert "XYZ" not in text and "client_id" not in text and "secret-q" not in text
    assert page.log[-1] == ("close",)


def test_ai06_page_failure_logs_step_and_structure_only(
    clock: FixedClock, runtime: BrowserRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    """입력칸이 끝내 나타나지 않으면 실패 단계·오류 종류·선택자 상태만 기록한다(질문 글자 없음)."""
    adapter, _, _ = _adapter(clock, runtime, [], visible=())
    with caplog.at_level("WARNING"):
        with pytest.raises(AiUnavailableError):
            adapter.classify_task(mask("민감한 질문 본문 XYZ"))
    text = caplog.text
    assert "단계=입력칸 대기" in text and "TimeoutError" in text and "prompt=0" in text
    assert "host=same" in text and "XYZ" not in text


def test_ai01_hidden_send_button_and_dead_enter_fall_back_to_form_submit(
    clock: FixedClock, runtime: BrowserRuntime
) -> None:
    """결정기록 0026: 보내기 버튼이 숨고 Enter로도 안 보내지면 폼 제출로 한 번만 보낸다."""
    adapter, page, _ = _adapter(clock, runtime, ["업무: 예"], chat=CHAT_NEW)
    page.enter_sends = False
    adapter.classify_task(mask("요청"))
    assert [e for e in page.log if e[0] == "request_submit"] == [("request_submit", "#q")]
    assert ("click", "#send") not in page.log
    assert len(page.answers) == 2  # 이전 답변 1 + 새 답변 1(중복 전송 없음)


def test_ai06_undeliverable_question_fails_fast_with_button_structure(
    clock: FixedClock, runtime: BrowserRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    """전송이 안 되면 답변을 90초 기다리지 않고 바로 실패하며, 버튼 구조만 남긴다."""
    adapter, page, fake_clock = _adapter(clock, runtime, [], chat=CHAT_NEW, timeout=90)
    page.enter_sends = False
    page.form_submit_sends = False
    with caplog.at_level("WARNING"):
        with pytest.raises(AiUnavailableError, match="보내지 못했습니다"):
            adapter.classify_task(mask("민감한 질문 본문 XYZ"))
    assert fake_clock.t - 1000.0 < 10  # 답변 대기(90초)에 들어가지 않음
    assert "단계=보내기" in caplog.text and "입력칸 주변 버튼=[" in caplog.text
    assert "XYZ" not in caplog.text
