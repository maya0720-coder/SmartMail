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
from urllib.parse import urljoin, urlsplit

from config.rules import RULES_DIR, read_toml
from config.settings import ConfigError
from domain.ai import AiAnswer, AiParseError, AiUnavailableError, Classification, RequestDigest
from integrations.ai.ground_diag import answer_shape, error_hint
from integrations.ai.ground_parallel import (
    ANSWER_TIMEOUT as ANSWER_TIMEOUT,  # 기존 import 호환(테스트)
)
from integrations.ai.ground_parallel import (
    FAILURE,
    POLL_SECONDS,
    AnswerWatcher,
    ChatJob,
    begin_chat,
    log_failure,
    run_chats,
)
from integrations.ai.ground_parallel import (
    NOT_SENT as NOT_SENT,
)
from integrations.ai.prompts import (
    MAX_ANSWER_CHARS,
    classify_prompt,
    parse_answer,
    parse_classification,
    parse_digests,
    suggest_prompt,
    summarize_prompt,
)
from integrations.ai.prompts_extra import (
    company_prompt,
    parse_company,
    parse_progress,
    progress_prompt,
)
from security.browser import EphemeralContext
from security.links import safe_link
from security.logging_policy import Site
from security.masking import MaskedText
from security.sessions import SessionExpiredError, SessionStore

logger = logging.getLogger(__name__)

DEFAULT_FIELDS_FILE: Final = RULES_DIR / "aiground_fields.toml"
# 권장값(결정기록 0008, A-6). 실제 사용 후 조정한다.
MAX_PROMPT_CHARS: Final = 9000  # 요약 2,000자 + 사례 5건×1,000자 + 안내문
MIN_INTERVAL_SECONDS: Final = 3.0  # 연속 호출 최소 간격
CALL_WINDOW_SECONDS: Final = 600.0  # 호출 수 제한 구간(10분)
MAX_CALLS_PER_WINDOW: Final = 20
# 그중 백그라운드(요약·분류) 상한. 나머지는 사용자가 누른 해결 방안 요청 몫(결정기록 0021)
MAX_BACKGROUND_CALLS_PER_WINDOW: Final = 14
CALL_MARGIN_SECONDS: Final = 10.0
# 앞선 호출 때문에 이보다 오래 기다려야 하면 보내지 않는다(바깥 시간 초과 계산을 넘지 않게)
MAX_QUEUE_WAIT_SECONDS: Final = 2 * MIN_INTERVAL_SECONDS
MAX_READ_CHARS: Final = 2 * MAX_ANSWER_CHARS  # 폴링마다 읽는 답변 글자 상한

NOT_CONFIRMED: Final = "AI-Ground 채팅 화면 필드가 확인되지 않아 아직 호출할 수 없습니다(확인 필요)"
LOGIN_REQUIRED: Final = "AI-Ground 로그인이 필요합니다"
TOO_MANY_CALLS: Final = "AI 호출이 많아 잠시 멈췄습니다. 10분 뒤 다시 시도해 주세요."


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
    watcher = AnswerWatcher(page, chat, before, now, MAX_READ_CHARS)
    while now() < deadline:
        text = watcher.poll()
        if text is not None:
            return text
        sleep(POLL_SECONDS)
    log_failure("답변 대기", TimeoutError(), page, chat, urlsplit(str(page.url)).netloc)
    raise AiUnavailableError(ANSWER_TIMEOUT)


class BrowserGllmAdapter:
    name = "ai-ground-gllm"
    send_interval_seconds = (
        MIN_INTERVAL_SECONDS  # 게이트웨이가 병렬 호출의 대기 시간을 계산할 때 쓴다
    )

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
        self._calls: deque[tuple[float, bool]] = deque()  # (호출 시각, 백그라운드 여부)

    def classify_task(self, summary: MaskedText) -> Classification:
        return parse_classification(self._ask(classify_prompt(summary), background=True))

    def suggest_solution(
        self, summary: MaskedText, similar_cases: Sequence[MaskedText]
    ) -> AiAnswer:
        return parse_answer(self._ask(suggest_prompt(summary, similar_cases), background=False))

    def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
        return parse_digests(self._ask(summarize_prompt(items), background=True), len(items))

    def company_name(self, domain: MaskedText) -> str | None:
        """도메인 문자열 하나로 회사명을 묻는다(결정기록 0027, 백그라운드 몫)."""
        return parse_company(self._ask(company_prompt(domain), background=True))

    def summarize_progress(self, items: Sequence[MaskedText]) -> str:
        """업무 진행 요약(결정기록 0028). 사용자가 누른 요청이라 전경 몫으로 보낸다."""
        return parse_progress(self._ask(progress_prompt(items), background=False))

    def summarize_many(
        self, batches: Sequence[Sequence[MaskedText]]
    ) -> list[list[RequestDigest | None] | AiUnavailableError]:
        """여러 묶음을 한 번의 브라우저 작업에서 여러 탭으로 동시에 진행한다(결정기록 0023).

        호출 순번은 `_reserve`가 상한 안에서 잡는다(상한이 모자라면 뒤쪽 묶음은 보내지 않고
        TOO_MANY_CALLS). 묶음마다 결과 또는 오류를 돌려주고,
        한 묶음의 실패가 다른 묶음을 막지 않는다.
        """
        chat = self._chat
        if not chat.ready:
            raise AiUnavailableError(NOT_CONFIRMED)
        context = self._context()
        url = self._url()
        prompts = [summarize_prompt(batch) for batch in batches]
        sendable = [i for i, p in enumerate(prompts) if len(p) <= MAX_PROMPT_CHARS]
        outcomes: list[list[RequestDigest | None] | AiUnavailableError] = [
            AiUnavailableError("AI로 보낼 내용이 너무 깁니다") for _ in batches
        ]
        if not sendable:
            return outcomes
        current, slots = self._reserve(len(sendable), background=True)
        for index in sendable[len(slots) :]:
            outcomes[index] = AiUnavailableError(TOO_MANY_CALLS)
        order = sendable[: len(slots)]
        jobs = [
            ChatJob(prompts[i], slot, slot + self._timeout)
            for i, slot in zip(order, slots, strict=True)
        ]

        def drive(raw: Any) -> list[str | AiUnavailableError]:
            return run_chats(raw, chat, url, jobs, self._now, self._sleep, MAX_READ_CHARS)

        try:
            answers = context.call(
                drive, timeout=(slots[-1] - current) + self._timeout + CALL_MARGIN_SECONDS
            )
        except AiUnavailableError:
            raise
        except Exception as exc:
            # 화면 단계 실패는 begin_chat이 이미 기록한다.
            # 여기는 브라우저 작업 자체의 실패(대기열 시간 초과 등)
            logger.warning("AI-Ground 브라우저 작업 실패 오류=%s", error_hint(exc))
            raise AiUnavailableError(FAILURE) from None
        for index, answer in zip(order, answers, strict=True):
            if isinstance(answer, AiUnavailableError):
                outcomes[index] = answer
                continue
            try:
                outcomes[index] = parse_digests(answer, len(batches[index]))
            except AiParseError as exc:  # 묶음을 1건씩 다시 보낼 수 있음
                # 답변 형식 진단: 글자 종류만 남긴 모양(내용 없음, 결정기록 0026)
                logger.warning("AI-Ground 요약 답변 해석 실패 모양=[%s]", answer_shape(answer))
                outcomes[index] = exc
            except AiUnavailableError as exc:
                outcomes[index] = exc
        return outcomes

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

    def _reserve(self, count: int, background: bool) -> tuple[float, list[float]]:
        """호출 상한(구간 상한·백그라운드 몫·최소 간격) 안에서 보낼 순번을 잡는다.

        최대 `count`개를 잡고(상한이 모자라면 적게), 보낼 시각 목록을 돌려준다.
        하나도 못 잡으면 거부.
        병렬 처리도 이 순번을 앞당기지 않는다(결정기록 0023).
        """
        with self._lock:
            current = self._now()
            while self._calls and current - self._calls[0][0] >= CALL_WINDOW_SECONDS:
                self._calls.popleft()
            room = MAX_CALLS_PER_WINDOW - len(self._calls)
            if background:
                # 요약이 밀려도 사용자의 해결 방안 요청 몫은 남겨 둔다
                room = min(room, MAX_BACKGROUND_CALLS_PER_WINDOW - sum(b for _, b in self._calls))
            if room <= 0:
                raise AiUnavailableError(TOO_MANY_CALLS)
            last = self._calls[-1][0] if self._calls else None
            first_wait = MIN_INTERVAL_SECONDS - (current - last) if last is not None else 0.0
            if first_wait > MAX_QUEUE_WAIT_SECONDS:
                raise AiUnavailableError(NOT_SENT)
            start = current + max(0.0, first_wait)
            slots = [start + n * MIN_INTERVAL_SECONDS for n in range(min(count, room))]
            self._calls.extend((slot, background) for slot in slots)
        return current, slots

    def _throttle(self, background: bool) -> None:
        current, slots = self._reserve(1, background)
        if slots[0] > current:
            self._sleep(slots[0] - current)

    def _ask(self, prompt: str, *, background: bool) -> str:
        chat = self._chat
        if not chat.ready:
            raise AiUnavailableError(NOT_CONFIRMED)
        if len(prompt) > MAX_PROMPT_CHARS:
            raise AiUnavailableError("AI로 보낼 내용이 너무 깁니다")
        context = self._context()
        url = self._url()
        self._throttle(background)
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
                before = begin_chat(page, chat, url, prompt, remaining_ms)
                return wait_for_answer(page, chat, before, deadline, self._now, self._sleep)
            finally:
                page.close()

        try:
            return context.call(drive, timeout=self._timeout + CALL_MARGIN_SECONDS)
        except AiUnavailableError:
            raise
        except Exception as exc:
            # 화면 단계 실패는 begin_chat이 이미 기록한다.
            # 여기는 브라우저 작업 자체의 실패(대기열 시간 초과 등)
            logger.warning("AI-Ground 브라우저 작업 실패 오류=%s", error_hint(exc))
            raise AiUnavailableError(FAILURE) from None
