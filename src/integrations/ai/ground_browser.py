"""AI-Ground g-llm 채팅 화면 자동화 어댑터(AI-01·05, 결정기록 0008).

AI-Ground는 공식 API가 없고 사내 SSO로 로그인한다. 그래서 04단계 로그인 창(Site.AIGROUND)으로
로그인한 비저장 브라우저 컨텍스트에서 채팅 화면에 질문을 넣고,
스트리밍 답변이 끝날 때까지 기다려 읽는다.

- 화면 선택자는 `config/rules/aiground_fields.toml`에서 읽는다.
  비어 있으면(확인 필요) 호출하지 않는다.
- 매 호출은 새 대화에서 시작한다(이전 질문이 다음 질문에 섞이지 않게).
- 호출 간격·10분당 호출 수를 제한한다. 보낸 내용이 AI-Ground에 저장·학습되므로 재전송을 줄인다.
- 답변 대기에는 자체 마감 시각이 있어, 바깥에서 시간 초과가 나도 브라우저 작업이 끝없이 돌지 않는다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Final
from urllib.parse import urljoin

from config.rules import RULES_DIR, read_toml
from config.settings import ConfigError
from domain.ai import AiAnswer, AiUnavailableError, Classification
from integrations.ai.prompts import (
    MAX_ANSWER_CHARS,
    classify_prompt,
    parse_answer,
    parse_classification,
    parse_summaries,
    suggest_prompt,
    summarize_prompt,
)
from security.browser import EphemeralContext
from security.links import safe_link
from security.logging_policy import Site
from security.masking import MaskedText
from security.sessions import SessionExpiredError, SessionStore

logger = logging.getLogger(__name__)

DEFAULT_FIELDS_FILE: Final = RULES_DIR / "aiground_fields.toml"
PAGE_TIMEOUT_MS: Final = 30_000
# 권장값(결정기록 0008, A-6). 실제 사용 후 조정한다.
MAX_PROMPT_CHARS: Final = 9000  # 요약 2,000자 + 사례 5건×1,000자 + 안내문
MIN_INTERVAL_SECONDS: Final = 3.0  # 연속 호출 최소 간격
CALL_WINDOW_SECONDS: Final = 600.0  # 호출 수 제한 구간(10분)
MAX_CALLS_PER_WINDOW: Final = 20
STABLE_SECONDS: Final = 2.0  # 답변 글자가 이 시간 동안 그대로면 스트리밍이 끝난 것으로 본다
POLL_SECONDS: Final = 0.5
CALL_MARGIN_SECONDS: Final = 10.0
# 앞선 호출 때문에 이보다 오래 기다려야 하면 보내지 않는다(바깥 시간 초과 계산을 넘지 않게)
MAX_QUEUE_WAIT_SECONDS: Final = 2 * MIN_INTERVAL_SECONDS
MAX_READ_CHARS: Final = 2 * MAX_ANSWER_CHARS  # 폴링마다 읽는 답변 글자 상한

NOT_CONFIRMED: Final = "AI-Ground 채팅 화면 필드가 확인되지 않아 아직 호출할 수 없습니다(확인 필요)"
LOGIN_REQUIRED: Final = "AI-Ground 로그인이 필요합니다"
TOO_MANY_CALLS: Final = "AI 호출이 많아 잠시 멈췄습니다. 10분 뒤 다시 시도해 주세요."
ANSWER_TIMEOUT: Final = "AI 응답이 늦어 중단했습니다. 잠시 후 다시 시도해 주세요."
FAILURE: Final = "AI-Ground 호출에 실패했습니다. 잠시 후 다시 시도해 주세요."
NOT_SENT: Final = "AI-Ground가 바빠 질문을 보내지 않았습니다. 잠시 후 다시 시도해 주세요."


@dataclass(frozen=True, slots=True)
class ChatFields:
    path: str
    new_chat: str
    model: str
    model_option: str
    prompt: str
    submit: str
    answer: str
    busy: str
    done: str

    @property
    def ready(self) -> bool:
        return all((self.path, self.prompt, self.answer))


def load_chat_fields(path: Path = DEFAULT_FIELDS_FILE) -> ChatFields:
    raw = read_toml(path).get("chat", {})
    names = [f.name for f in fields(ChatFields)]
    if not isinstance(raw, dict) or set(raw) - set(names):
        raise ConfigError("aiground_fields: [chat] 키 형식 오류")
    if not all(isinstance(raw.get(n, ""), str) for n in names):
        raise ConfigError("aiground_fields: [chat] 값은 문자열이어야 합니다")
    return ChatFields(**{n: str(raw.get(n, "")).strip() for n in names})


def wait_for_answer(
    page: Any,
    chat: ChatFields,
    before: tuple[int, int],
    deadline: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> str:
    """새 답변이 나타나고 스트리밍이 끝날 때까지 기다린다. 마감 시각이 지나면 시간 초과."""
    answers_before, done_before = before
    last = ""
    stable_since: float | None = None
    while now() < deadline:
        answers = page.locator(chat.answer)
        count = answers.count()
        if count > answers_before:
            text = str(answers.nth(count - 1).inner_text())[:MAX_READ_CHARS].strip()
            busy = bool(chat.busy) and page.locator(chat.busy).count() > 0
            done = not chat.done or page.locator(chat.done).count() > done_before
            if text and text == last:
                stable_since = stable_since if stable_since is not None else now()
                if not busy and done and now() - stable_since >= STABLE_SECONDS:
                    return text
            else:
                last, stable_since = text, None
        sleep(POLL_SECONDS)
    raise AiUnavailableError(ANSWER_TIMEOUT)


class BrowserGllmAdapter:
    name = "ai-ground-gllm"

    def __init__(
        self,
        base_url: str,
        sessions: SessionStore,
        chat: ChatFields,
        *,
        timeout_seconds: float,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = base_url.rstrip("/") + "/"
        self._sessions = sessions
        self._chat = chat
        self._timeout = timeout_seconds
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()

    def classify_task(self, summary: MaskedText) -> Classification:
        return parse_classification(self._ask(classify_prompt(summary)))

    def suggest_solution(
        self, summary: MaskedText, similar_cases: Sequence[MaskedText]
    ) -> AiAnswer:
        return parse_answer(self._ask(suggest_prompt(summary, similar_cases)))

    def summarize_requests(self, items: Sequence[MaskedText]) -> list[str | None]:
        return parse_summaries(self._ask(summarize_prompt(items)), len(items))

    def _context(self) -> EphemeralContext:
        handle = self._sessions.current(Site.AIGROUND)
        if handle is None:
            raise AiUnavailableError(LOGIN_REQUIRED)
        try:
            native = self._sessions.use(handle)
        except SessionExpiredError:
            raise AiUnavailableError(LOGIN_REQUIRED) from None
        if not isinstance(native, EphemeralContext):
            raise AiUnavailableError("AI-Ground는 브라우저(SSO) 로그인 세션이 필요합니다")
        return native

    def _url(self) -> str:
        url = urljoin(self._base, self._chat.path.lstrip("/"))
        # https + 같은 호스트·포트만 허용(userinfo·다른 포트·http 거부)
        if safe_link(url, [self._base]) is None:
            raise AiUnavailableError("AI-Ground 주소 설정이 올바르지 않습니다")
        return url

    def _throttle(self) -> None:
        with self._lock:
            current = self._now()
            while self._calls and current - self._calls[0] >= CALL_WINDOW_SECONDS:
                self._calls.popleft()
            if len(self._calls) >= MAX_CALLS_PER_WINDOW:
                raise AiUnavailableError(TOO_MANY_CALLS)
            wait = MIN_INTERVAL_SECONDS - (current - self._calls[-1]) if self._calls else 0.0
            if wait > MAX_QUEUE_WAIT_SECONDS:
                raise AiUnavailableError(NOT_SENT)
            self._calls.append(current + max(0.0, wait))
        if wait > 0:
            self._sleep(wait)

    def _ask(self, prompt: str) -> str:
        chat = self._chat
        if not chat.ready:
            raise AiUnavailableError(NOT_CONFIRMED)
        if len(prompt) > MAX_PROMPT_CHARS:
            raise AiUnavailableError("AI로 보낼 내용이 너무 깁니다")
        context = self._context()
        url = self._url()
        self._throttle()
        # 포기 시각은 호출한 시점 기준이다. 브라우저 대기열에서 늦게 꺼내져도 이 시각이 지났으면
        # 질문을 보내지 않는다(바깥에서 이미 실패로 처리한 요청이 나중에 전송되지 않게).
        deadline = self._now() + self._timeout

        def remaining_ms() -> int:
            left = deadline - self._now()
            if left <= 0:
                raise AiUnavailableError(NOT_SENT)
            return max(1, int(left * 1000))

        def drive(raw: Any) -> str:
            remaining_ms()
            page = raw.new_page()
            try:
                # 화면 단계마다의 대기도 남은 시간 안으로 묶는다
                page.set_default_timeout(remaining_ms())
                page.goto(url, timeout=min(PAGE_TIMEOUT_MS, remaining_ms()))
                if chat.new_chat:
                    page.click(chat.new_chat)
                if chat.model and chat.model_option:
                    page.click(chat.model)
                    page.click(chat.model_option)
                before = (
                    page.locator(chat.answer).count(),
                    page.locator(chat.done).count() if chat.done else 0,
                )
                remaining_ms()
                page.fill(chat.prompt, prompt)
                remaining_ms()  # 보내기 직전 마지막 확인
                if chat.submit:
                    page.click(chat.submit)
                else:
                    page.press(chat.prompt, "Enter")
                return wait_for_answer(page, chat, before, deadline, self._now, self._sleep)
            finally:
                page.close()

        try:
            return context.call(drive, timeout=self._timeout + CALL_MARGIN_SECONDS)
        except AiUnavailableError:
            raise
        except Exception:
            logger.warning("AI-Ground 화면 자동화 실패")
            raise AiUnavailableError(FAILURE) from None
