"""그룹웨어 보안문자(CAPTCHA) 처리 규칙(AUTH-01·09, 결정기록 0025).

- 보안문자 이미지는 로그인 중인 같은 비저장 컨텍스트의 page에서 요소 스크린샷으로만 얻는다.
  별도 HTTP 요청으로 다시 받으면 새 보안문자가 발급돼 세션 값과 어긋난다.
- 이미지·입력값은 메모리에서만 지나가며 저장·로그에 남기지 않는다.
- 오류(알림창·화면 오류 영역)는 원문을 남기지 않고 범주로만 분류한다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from config.settings import ConfigError
from security.credentials import LoginFailedError

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES: Final = 200 * 1024  # 보안문자 이미지 크기 상한
REFRESH_TOKEN: Final = "refresh-request"  # noqa: S105 - 암호 아님: '새 이미지' 요청 표지값
MAX_REFRESHES: Final = 5  # 새 이미지 요청 허용 횟수(한 번의 보안문자 단계)
MAX_STALE: Final = 2  # 이미지가 바뀌어 다시 묻는 최대 횟수
SUBMIT_FALLBACK_SECONDS: Final = (
    3.0  # Enter 제출 뒤 변화가 없으면 제출 버튼을 누르기까지 기다리는 시간
)
REFRESH_WAIT_SECONDS: Final = 3.0  # 새 이미지 요청 뒤 이미지가 바뀌길 기다리는 시간
POLL_SECONDS: Final = 0.25
ERROR_TEXT_LIMIT: Final = 300  # 화면 오류 영역에서 읽는 글자 상한(분류에만 쓰고 버림)
CASE_MODES: Final = ("keep", "upper", "lower")

CAPTCHA_FAILED: Final = "보안문자 확인을 완료하지 못했습니다."
CAPTCHA_PAGE_CHANGED: Final = (
    "로그인 화면이 바뀌어 입력을 중단했습니다. 처음부터 다시 로그인해 주세요."
)

# 보안문자 입력 요청: (이미지 PNG, 재입력 여부, 남은 초) → 입력값·REFRESH_TOKEN. 취소면 None
AskCaptcha = Callable[[bytes, bool, float], "str | None"]


class CaptchaFailedError(LoginFailedError):
    """보안문자를 끝내 통과하지 못함(메시지에 값 없음)."""


class ErrorCategory(Enum):
    CAPTCHA = "보안문자 불일치"
    CREDENTIALS = "아이디·암호 오류"
    OTP = "인증문자 안내"
    OTHER = "기타"


_WORDS: Final = (
    (
        ErrorCategory.CAPTCHA,
        ("보안문자", "보안 문자", "자동입력방지", "자동 입력 방지", "captcha", "캡차"),
    ),
    (ErrorCategory.OTP, ("인증번호", "인증 번호", "인증문자", "문자 인증", "otp", "sms")),
    (ErrorCategory.CREDENTIALS, ("아이디", "비밀번호", "암호", "패스워드", "password", "계정")),
)


def classify_message(message: str) -> ErrorCategory:
    """오류 글자를 범주로만 분류한다. 글자 자체는 호출한 쪽이 곧바로 버린다."""
    lowered = message[:ERROR_TEXT_LIMIT].lower()
    for category, words in _WORDS:
        if any(word in lowered for word in words):
            return category
    return ErrorCategory.OTHER


@dataclass(frozen=True, slots=True)
class CaptchaPolicy:
    case: str
    min_len: int
    max_len: int
    max_tries: int


def parse_policy(case: str, length: str, max_tries: str) -> CaptchaPolicy:
    """login_fields.toml 문자열 설정을 검증한다. 형식 오류는 시작 시 실패시킨다."""
    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", length.strip())
    if case not in CASE_MODES or match is None or not max_tries.strip().isdigit():
        raise ConfigError("login_fields: 보안문자 설정 형식 오류")
    low, high, tries = int(match.group(1)), int(match.group(2)), int(max_tries)
    if not (1 <= low <= high <= 32 and 1 <= tries <= 3):
        raise ConfigError("login_fields: 보안문자 설정 범위 오류")
    return CaptchaPolicy(case, low, high, tries)


def normalize(raw: str, policy: CaptchaPolicy) -> str | None:
    """앞뒤·내부 공백 제거, 대소문자 정책 적용, 영문·숫자·길이 검사. 잘못되면 None."""
    value = "".join(raw.split())
    if policy.case == "upper":
        value = value.upper()
    elif policy.case == "lower":
        value = value.lower()
    if not re.fullmatch(r"[0-9A-Za-z]+", value):
        return None
    return value if policy.min_len <= len(value) <= policy.max_len else None


class CaptchaGate:
    """보안문자 이미지 표시 → 입력 → 같은 화면 입력칸에 채우기. page는 FormLoginPage."""

    def __init__(
        self,
        page: Any,
        fields: Any,
        policy: CaptchaPolicy,
        ask: AskCaptcha | None,
        *,
        now: Callable[[], float],
        sleep: Callable[[float], None],
        deadline: float,
    ) -> None:
        self._page, self._f, self._policy = page, fields, policy
        self._ask, self._now, self._sleep, self._deadline = ask, now, sleep, deadline
        self.solved = 0  # 사용자가 값을 넣어 준 횟수
        self.last_src = ""  # 마지막으로 값을 채운 이미지의 src(화면이 바뀌었는지 비교)

    @property
    def max_tries(self) -> int:
        return self._policy.max_tries

    def present(self) -> bool:
        return bool(self._page.visible(self._f.captcha_image)) and bool(
            self._page.visible(self._f.captcha_input)
        )

    def image_changed(self) -> bool:
        """값을 채운 뒤 이미지가 바뀌었으면 화면이 새로 불러와진 것(= 값이 반려됨)."""
        return bool(self.last_src) and self._src() != self.last_src

    def _src(self) -> str:
        return str(self._page.attribute(self._f.captcha_image, "src"))

    def solve(self, retry: bool) -> None:
        """이미지를 떠서 사용자에게 묻고 입력칸에 채운다. 제출은 호출한 쪽이 한다."""
        if self._ask is None:
            raise CaptchaFailedError(CAPTCHA_FAILED)
        stale = refreshes = 0
        while True:
            url, src = self._page.url, self._src()
            png = self._page.capture(self._f.captcha_image)
            if not png or len(png) > MAX_IMAGE_BYTES:
                raise CaptchaFailedError(CAPTCHA_FAILED)
            code = self._ask(png, retry, max(0.0, self._deadline - self._now()))
            del png  # 이미지는 사용자에게 보여 준 뒤 바로 버린다
            if not code:
                raise CaptchaFailedError(CAPTCHA_FAILED)  # 취소·시간 초과
            if code == REFRESH_TOKEN:
                refreshes += 1
                if refreshes > MAX_REFRESHES or not self._page.visible(self._f.captcha_refresh):
                    raise CaptchaFailedError(CAPTCHA_FAILED)
                self._page.click(self._f.captcha_refresh)
                self._wait_image_change(src)
                continue
            value = normalize(code, self._policy)
            if value is None:
                raise CaptchaFailedError(CAPTCHA_FAILED)
            # 사용자가 보는 동안 화면이 다시 불러와졌으면(=보안문자 재발급) 새 이미지로 다시 묻는다
            if self._page.url != url or self._src() != src:
                stale += 1
                if stale > MAX_STALE:
                    raise CaptchaFailedError(CAPTCHA_FAILED)
                retry = True
                continue
            self._page.fill(self._f.captcha_input, value)
            self.last_src, self.solved = src, self.solved + 1
            return

    def _wait_image_change(self, before: str) -> None:
        until = self._now() + REFRESH_WAIT_SECONDS
        while self._now() < until and self._src() == before:
            self._sleep(POLL_SECONDS)

    def require_filled(self) -> None:
        """재입력 때 화면이 암호칸을 비웠으면 빈 암호를 제출하지 않고 중단한다."""
        if not self._page.has_value(self._f.concealed_input):
            raise CaptchaFailedError(CAPTCHA_PAGE_CHANGED)


def log_hidden_inputs(page: Any) -> None:
    """제출 직전 폼 안 hidden 입력의 개수와 이름만 기록한다(값 금지, 진단용)."""
    try:
        names = [str(n) for n in page.hidden_names()]
    except Exception:
        return
    shown = ", ".join("".join(ch for ch in n[:40] if ch.isprintable()) for n in names[:20])
    logger.info("그룹웨어 제출 직전 hidden 입력 %d개: [%s]", len(names), shown)


def submit_with_fallback(
    page: Any,
    key_selector: str,
    submit_selector: str,
    watch: Callable[[], tuple[object, ...]],
    *,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """입력칸에서 Enter로 제출하고, 일정 시간 변화가 없으면 제출 버튼을 누른다.

    watch 값(주소·알림창 수·이동 횟수 등)이 달라졌으면 제출된 것이므로 버튼을 누르지 않는다.
    """
    log_hidden_inputs(page)
    before = watch()
    page.press(key_selector, "Enter")
    until = now() + SUBMIT_FALLBACK_SECONDS
    while now() < until:
        if watch() != before:
            return
        sleep(POLL_SECONDS)
    if watch() == before and page.visible(submit_selector):
        logger.info("그룹웨어 Enter 제출에 변화가 없어 제출 버튼을 눌렀습니다")
        page.click(submit_selector)
