"""위키 페이지 본문 읽기(브라우저 화면 읽기, N4, 결정기록 0037).

- 위키 로그인 세션(비저장 브라우저 컨텍스트, 04단계)으로 페이지를 열고 본문 영역 HTML만 가져온다.
  사용자가 브라우저로 보는 것과 같은 화면 읽기이며 REST API는 쓰지 않는다.
- 링크와 이동 후 주소는 위키 기준 주소와 출처(https·호스트·포트)가 같아야 한다(SSRF 방지).
- 본문 선택자·크기 상한·제한 시간은 `config/rules/wiki_read.toml`에서 읽는다.
- 읽은 HTML은 저장·로그하지 않고 호출한 쪽(구조 추출기)에 바로 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from config.rules import RULES_DIR, read_toml
from config.settings import ConfigError
from domain.weekly.service import WikiLoginRequiredError, WikiUnavailableError
from security.browser import EphemeralContext
from security.links import safe_link
from security.logging_policy import Site
from security.sessions import SessionExpiredError, SessionStore

DEFAULT_READ_FILE: Final = RULES_DIR / "wiki_read.toml"
CALL_MARGIN_SECONDS: Final = 10.0
MAX_READ_BYTES: Final = 500_000
LOGIN_REQUIRED: Final = "위키 로그인이 필요합니다"
FOREIGN_LINK: Final = "위키 기준 주소와 다른 링크라 조회하지 않습니다"
TOO_LARGE: Final = "위키 본문이 너무 커서 읽지 않았습니다(500KB 이하)"
READ_FAILED: Final = "위키 페이지 본문을 읽지 못했습니다(본문 선택자 확인 필요)"
HTML_SCRIPT: Final = "e => e.innerHTML"


@dataclass(frozen=True, slots=True)
class WikiReadRules:
    content_selector: str = "#main-content"
    max_bytes: int = MAX_READ_BYTES
    timeout_seconds: float = 30.0


def load_wiki_read(path: Path = DEFAULT_READ_FILE) -> WikiReadRules:
    """위키 읽기 규칙. 형식 오류는 시작 시 실패시킨다."""
    data = read_toml(path)
    unknown = set(data) - {"content_selector", "max_bytes", "timeout_seconds"}
    if unknown:
        raise ConfigError(f"{path.name}: 알 수 없는 키 {sorted(unknown)}")
    defaults = WikiReadRules()
    selector = data.get("content_selector", defaults.content_selector)
    max_bytes = data.get("max_bytes", MAX_READ_BYTES)
    timeout = data.get("timeout_seconds", defaults.timeout_seconds)
    if not isinstance(selector, str) or not 0 < len(selector) <= 200:
        raise ConfigError(f"{path.name}: content_selector 형식 오류")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise ConfigError(f"{path.name}: max_bytes 형식 오류")
    if not 0 < max_bytes <= MAX_READ_BYTES:
        raise ConfigError(f"{path.name}: max_bytes는 1~{MAX_READ_BYTES}")
    if not isinstance(timeout, int | float) or isinstance(timeout, bool) or not 0 < timeout <= 120:
        raise ConfigError(f"{path.name}: timeout_seconds는 1~120")
    return WikiReadRules(selector, max_bytes, float(timeout))


class BrowserWikiReader:
    def __init__(self, base_url: str, sessions: SessionStore, rules: WikiReadRules) -> None:
        self._base = base_url
        self._sessions = sessions
        self._rules = rules

    def has_session(self) -> bool:
        return self._sessions.current(Site.WIKI) is not None

    def _context(self) -> EphemeralContext:
        handle = self._sessions.current(Site.WIKI)
        if handle is None:
            raise WikiLoginRequiredError(LOGIN_REQUIRED)
        try:
            native = self._sessions.use(handle)
        except SessionExpiredError:
            raise WikiLoginRequiredError(LOGIN_REQUIRED) from None
        if not isinstance(native, EphemeralContext):
            raise WikiUnavailableError("위키 양식 읽기는 브라우저 로그인 세션이 필요합니다")
        return native

    def read_page_html(self, link: str) -> str:
        url = safe_link(link, [self._base])
        if url is None:
            raise WikiUnavailableError(FOREIGN_LINK)
        context = self._context()
        rules = self._rules
        wait_ms = int(rules.timeout_seconds * 1000)
        base = self._base

        def drive(raw: Any) -> str:
            page = raw.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=wait_ms)
                if safe_link(str(page.url), [base]) is None:
                    raise WikiUnavailableError(FOREIGN_LINK)  # 다른 사이트로 이동됨
                if "login" in str(page.url).lower():
                    raise WikiLoginRequiredError(LOGIN_REQUIRED)  # 세션 만료로 로그인 화면
                element = page.wait_for_selector(rules.content_selector, timeout=wait_ms)
                if element is None:
                    raise WikiUnavailableError(READ_FAILED)
                return str(element.evaluate(HTML_SCRIPT))
            finally:
                page.close()

        try:
            body = context.call(drive, timeout=rules.timeout_seconds + CALL_MARGIN_SECONDS)
        except WikiUnavailableError:
            raise
        except Exception:
            raise WikiUnavailableError(READ_FAILED) from None
        if len(body.encode("utf-8")) > rules.max_bytes:
            raise WikiUnavailableError(TOO_LARGE)
        return body
