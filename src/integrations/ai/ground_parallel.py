"""AI-Ground 채팅 화면 공통 동작: 답변 감시기와 여러 탭 동시 처리(결정기록 0023).

- Playwright 동기 API는 브라우저 전용 스레드 하나에서만 쓸 수 있다. 그래서 스레드를 늘리지 않고,
  한 번의 브라우저 작업 안에서 여러 탭(page)에 질문을 차례로 보내고 모두 번갈아 폴링해 답을 모은다.
- 질문을 보내는 시각(`send_at`)은 어댑터가 호출 상한(최소 간격·구간 상한)으로 이미 정해 준다.
  이 모듈은 그 시각을 앞당기지 않는다(사용 통제 유지, 결정기록 0008·0018·0021).
- 탭마다 자체 마감 시각이 있어 한 탭이 늦어도 다른 탭의 답은 살린다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from domain.ai import AiUnavailableError
from integrations.ai.ground_diag import describe_chat_page, error_hint
from integrations.ai.ground_send import SendNotConfirmedError, form_buttons, send_and_confirm

logger = logging.getLogger(__name__)

STABLE_SECONDS: Final = 2.0  # 답변 글자가 이 시간 동안 그대로면 스트리밍이 끝난 것으로 본다
POLL_SECONDS: Final = 0.5
PAGE_TIMEOUT_MS: Final = 30_000
READY_TIMEOUT_MS: Final = 20_000  # 채팅 화면(입력칸)이 그려지기를 기다리는 최대 시간

ANSWER_TIMEOUT: Final = "AI 응답이 늦어 중단했습니다. 잠시 후 다시 시도해 주세요."
FAILURE: Final = "AI-Ground 호출에 실패했습니다. 잠시 후 다시 시도해 주세요."
NOT_SENT: Final = "AI-Ground가 바빠 질문을 보내지 않았습니다. 잠시 후 다시 시도해 주세요."
NOT_DELIVERED: Final = "AI-Ground에 질문을 보내지 못했습니다(보내기 버튼 확인 필요)."
SESSION_EXPIRED: Final = "AI-Ground 로그인이 만료되었습니다. 다시 로그인해 주세요."


class ChatSelectors(Protocol):
    """채팅 화면 선택자(읽기 전용). `ChatFields`가 이를 만족한다."""

    @property
    def path(self) -> str: ...

    @property
    def new_chat(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def model_option(self) -> str: ...

    @property
    def prompt(self) -> str: ...

    @property
    def submit(self) -> str: ...

    @property
    def answer(self) -> str: ...

    @property
    def busy(self) -> str: ...

    @property
    def done(self) -> str: ...


class AnswerWatcher:
    """답변이 새로 나타나 스트리밍이 끝났는지 한 번씩 확인한다(비차단). 끝나면 답변 글자."""

    def __init__(
        self,
        page: Any,
        chat: ChatSelectors,
        before: tuple[int, int],
        now: Callable[[], float],
        max_chars: int,
    ) -> None:
        self._page = page
        self._chat = chat
        self._before = before
        self._now = now
        self._max_chars = max_chars
        self._last = ""
        self._stable_since: float | None = None

    def poll(self) -> str | None:
        chat, page = self._chat, self._page
        answers_before, done_before = self._before
        answers = page.locator(chat.answer)
        count = answers.count()
        if count <= answers_before:
            return None
        text = str(answers.nth(count - 1).inner_text())[: self._max_chars].strip()
        busy = bool(chat.busy) and page.locator(chat.busy).count() > 0
        done = not chat.done or page.locator(chat.done).count() > done_before
        if text and text == self._last:
            if self._stable_since is None:
                self._stable_since = self._now()
            if not busy and done and self._now() - self._stable_since >= STABLE_SECONDS:
                return text
        else:
            self._last, self._stable_since = text, None
        return None


def _ready_timeout_ms(remaining_ms: Callable[[], int]) -> int:
    return min(READY_TIMEOUT_MS, remaining_ms())


def begin_chat(
    page: Any, chat: ChatSelectors, url: str, prompt: str, remaining_ms: Callable[[], int]
) -> tuple[int, int]:
    """새 대화를 열고 질문을 보낸다. 보내기 전 답변 수(이전 답변 구분용)를 돌려준다.

    실패하면 단계·오류 종류·화면 구조만 기록하고 AiUnavailableError로 바꾼다(결정기록 0026).
    """
    expected_host = urlsplit(url).netloc
    step = "열기"
    try:
        page.set_default_timeout(remaining_ms())  # 화면 단계마다의 대기도 남은 시간 안으로 묶는다
        page.goto(url, timeout=min(PAGE_TIMEOUT_MS, remaining_ms()), wait_until="domcontentloaded")
        step = "입력칸 대기"
        # 채팅 화면(SPA)이 그려질 때까지 기다린다.
        # 다른 호스트로 넘어갔으면(SSO 로그인 화면) 로그인 만료다.
        try:
            page.locator(chat.prompt).first.wait_for(
                state="visible", timeout=_ready_timeout_ms(remaining_ms)
            )
        except Exception:
            if urlsplit(str(page.url)).netloc != expected_host:
                raise AiUnavailableError(SESSION_EXPIRED) from None
            raise
        if chat.new_chat:
            step = "새 대화"
            # 좁은 화면에서는 대화 목록(새 대화 버튼)이 접혀 숨겨질 수 있다.
            # 첫 화면이 이미 새 대화이므로 버튼이 보일 때만 누른다(숨은 버튼 대기 금지).
            button = page.locator(chat.new_chat).first
            if button.is_visible():
                button.click()
                page.locator(chat.prompt).first.wait_for(
                    state="visible", timeout=_ready_timeout_ms(remaining_ms)
                )
        if chat.model and chat.model_option:
            step = "모델 선택"
            page.click(chat.model)
            page.click(chat.model_option)
        before = (
            page.locator(chat.answer).count(),
            page.locator(chat.done).count() if chat.done else 0,
        )
        step = "질문 입력"
        remaining_ms()
        page.locator(chat.prompt).first.fill(prompt)
        step = "보내기"
        remaining_ms()  # 보내기 직전 마지막 확인
        # 실제로 전송됐는지 확인하고, 아니면 다른 보내기 방법을 차례로 쓴다(결정기록 0026)
        method = send_and_confirm(page, chat.prompt, chat.submit, chat.answer)
        logger.info("AI-Ground 질문 전송 방법=%s", method)
        return before
    except SendNotConfirmedError as exc:
        log_failure(step, exc, page, chat, expected_host)
        logger.warning("AI-Ground 입력칸 주변 버튼=[%s]", form_buttons(page, chat.prompt))
        raise AiUnavailableError(NOT_DELIVERED) from None
    except AiUnavailableError as exc:
        log_failure(step, exc, page, chat, expected_host)
        raise
    except Exception as exc:
        log_failure(step, exc, page, chat, expected_host)
        raise AiUnavailableError(FAILURE) from None


def log_failure(step: str, exc: BaseException, page: Any, chat: ChatSelectors, host: str) -> None:
    """실패 단계·오류 종류·화면 구조만 남긴다(질문·답변·화면 글자 없음)."""
    logger.warning(
        "AI-Ground 화면 자동화 실패 단계=%s 오류=%s 화면=[%s]",
        step,
        error_hint(exc),
        describe_chat_page(page, chat, host),
    )


@dataclass(frozen=True, slots=True)
class ChatJob:
    prompt: str
    send_at: float  # 이 시각 전에는 보내지 않는다(호출 상한이 정한 순번)
    deadline: float  # 이 시각까지 답이 없으면 시간 초과


def _close(page: Any) -> None:
    try:
        page.close()
    except Exception:
        logger.warning("AI-Ground 탭 닫기 실패")


def run_chats(
    raw: Any,
    chat: ChatSelectors,
    url: str,
    jobs: list[ChatJob],
    now: Callable[[], float],
    sleep: Callable[[float], None],
    max_chars: int,
) -> list[str | AiUnavailableError]:
    """브라우저 스레드 안에서 여러 탭을 동시에 진행한다. 탭마다 답변 글자 또는 오류를 돌려준다."""
    results: list[str | AiUnavailableError | None] = [None] * len(jobs)
    host = urlsplit(url).netloc
    pages: dict[int, Any] = {}
    watchers: dict[int, AnswerWatcher] = {}

    def finish(index: int, value: str | AiUnavailableError) -> None:
        results[index] = value
        page = pages.pop(index, None)
        watchers.pop(index, None)
        if page is not None:
            _close(page)

    def start(index: int, job: ChatJob) -> None:
        def remaining_ms() -> int:
            left = job.deadline - now()
            if left <= 0:
                raise AiUnavailableError(NOT_SENT)
            return max(1, int(left * 1000))

        try:
            remaining_ms()
            page = raw.new_page()
            pages[index] = page
            before = begin_chat(page, chat, url, job.prompt, remaining_ms)
            watchers[index] = AnswerWatcher(page, chat, before, now, max_chars)
        except AiUnavailableError as exc:
            finish(index, exc)
        except Exception as exc:  # 탭 열기 실패(브라우저 닫힘 등)
            logger.warning("AI-Ground 탭 열기 실패 오류=%s", error_hint(exc))
            finish(index, AiUnavailableError(FAILURE))

    try:
        while any(r is None for r in results):
            for index, job in enumerate(jobs):
                if results[index] is not None:
                    continue
                current = now()
                if index in watchers:
                    if current >= job.deadline:
                        log_failure("답변 대기", TimeoutError(), pages[index], chat, host)
                        finish(index, AiUnavailableError(ANSWER_TIMEOUT))
                        continue
                    try:
                        text = watchers[index].poll()
                    except Exception as exc:
                        log_failure("답변 읽기", exc, pages[index], chat, host)
                        finish(index, AiUnavailableError(FAILURE))
                        continue
                    if text is not None:
                        finish(index, text)
                elif current >= job.deadline:
                    finish(index, AiUnavailableError(NOT_SENT))
                elif current >= job.send_at:
                    start(index, job)
            if all(r is not None for r in results):
                break
            sleep(POLL_SECONDS)
    finally:
        for page in list(pages.values()):
            _close(page)
    return [r if r is not None else AiUnavailableError(FAILURE) for r in results]
