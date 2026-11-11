"""G-Knowledge 브라우저 자동화(GK-03·04·05, SRCH-02).

- 로그인한 비저장 브라우저 컨텍스트(04단계 `EphemeralContext`)로만 동작한다.
- 화면 필드 선택자는 `config/rules/gknowledge_fields.toml`에서 읽는다. 비어 있으면(확인 필요)
  자동화를 하지 않고 `KnowledgeUnavailableError`로 안내한다(추측 구현 금지).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Final
from urllib.parse import urljoin, urlsplit

from config.rules import RULES_DIR, read_toml
from config.settings import ConfigError
from domain.knowledge import (
    KnowledgeHit,
    KnowledgeUnavailableError,
    MaskedDraft,
    RegisterResult,
)
from security.browser import EphemeralContext
from security.links import safe_link
from security.logging_policy import Site
from security.masking import MaskedText
from security.sessions import SessionExpiredError, SessionStore

logger = logging.getLogger(__name__)

DEFAULT_FIELDS_FILE: Final = RULES_DIR / "gknowledge_fields.toml"
PAGE_TIMEOUT_MS: Final = 30_000
MAX_RESULTS: Final = 10
NOT_CONFIRMED: Final = (
    "G-Knowledge 화면 필드가 확인되지 않아 자동 등록·검색을 할 수 없습니다(확인 필요)"
)
LOGIN_REQUIRED: Final = "G-Knowledge 로그인이 필요합니다"


@dataclass(frozen=True, slots=True)
class RegisterFields:
    path: str
    title: str
    body: str
    tags: str
    submit: str
    result_link: str

    @property
    def ready(self) -> bool:
        return all((self.path, self.title, self.body, self.submit))


@dataclass(frozen=True, slots=True)
class SearchFields:
    path: str
    query: str
    submit: str
    item: str
    item_title: str
    item_link: str

    @property
    def ready(self) -> bool:
        return all((self.path, self.query, self.submit, self.item, self.item_link))


def _section(data: dict[str, object], name: str, cls: type[Any]) -> Any:
    raw = data.get(name, {})
    if not isinstance(raw, dict):
        raise ConfigError(f"gknowledge_fields: [{name}] 형식 오류")
    names = [f.name for f in fields(cls)]
    unknown = set(raw) - set(names)
    if unknown or not all(isinstance(raw.get(n, ""), str) for n in names):
        raise ConfigError(f"gknowledge_fields: [{name}] 키 또는 값 형식 오류")
    return cls(**{n: str(raw.get(n, "")).strip() for n in names})


def load_fields(path: Path = DEFAULT_FIELDS_FILE) -> tuple[RegisterFields, SearchFields]:
    data = read_toml(path)
    return _section(data, "register", RegisterFields), _section(data, "search", SearchFields)


class BrowserKnowledgeStore:
    name = "g-knowledge"

    def __init__(
        self,
        base_url: str,
        sessions: SessionStore,
        register_fields: RegisterFields,
        search_fields: SearchFields,
    ) -> None:
        self._base = base_url.rstrip("/") + "/"
        self._host = urlsplit(base_url).hostname
        self._sessions = sessions
        self._register = register_fields
        self._search = search_fields

    def _context(self) -> EphemeralContext:
        handle = self._sessions.current(Site.GKNOWLEDGE)
        if handle is None:
            raise KnowledgeUnavailableError(LOGIN_REQUIRED)
        try:
            native = self._sessions.use(handle)
        except SessionExpiredError:
            raise KnowledgeUnavailableError(LOGIN_REQUIRED) from None
        if not isinstance(native, EphemeralContext):
            raise KnowledgeUnavailableError("G-Knowledge는 브라우저 로그인 세션이 필요합니다")
        return native

    def _url(self, path: str) -> str:
        url = urljoin(self._base, path.lstrip("/"))
        if urlsplit(url).hostname != self._host:
            raise KnowledgeUnavailableError("G-Knowledge 주소 설정이 올바르지 않습니다")
        return url

    def _same_site(self, link: str) -> str:
        # https + 같은 호스트·포트만 허용(javascript:, http 다운그레이드, 다른 포트 거부)
        return safe_link(link, [self._base], base_for_relative=self._base) or ""

    def register(self, draft: MaskedDraft) -> RegisterResult:
        fields_ = self._register
        if not fields_.ready:
            raise KnowledgeUnavailableError(NOT_CONFIRMED)
        context = self._context()
        url = self._url(fields_.path)

        def drive(raw: Any) -> str:
            page = raw.new_page()
            try:
                page.goto(url, timeout=PAGE_TIMEOUT_MS)
                page.fill(fields_.title, draft.title.value)
                page.fill(fields_.body, draft.body.value)
                if fields_.tags and draft.tags:
                    page.fill(fields_.tags, ", ".join(t.value for t in draft.tags))
                page.click(fields_.submit)
                page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                if fields_.result_link:
                    return str(page.get_attribute(fields_.result_link, "href") or "")
                return str(page.url)
            finally:
                page.close()

        try:
            link = context.call(drive)
        except KnowledgeUnavailableError:
            raise
        except Exception:
            logger.warning("G-Knowledge 등록 자동화 실패")
            raise KnowledgeUnavailableError("G-Knowledge 등록에 실패했습니다") from None
        safe = self._same_site(link)
        if not safe:
            raise KnowledgeUnavailableError("등록 결과 링크를 확인하지 못했습니다")
        return RegisterResult(safe)

    def search(self, query: MaskedText) -> list[KnowledgeHit]:
        fields_ = self._search
        if not fields_.ready:
            raise KnowledgeUnavailableError(NOT_CONFIRMED)
        context = self._context()
        url = self._url(fields_.path)

        def drive(raw: Any) -> list[tuple[str, str]]:
            page = raw.new_page()
            try:
                page.goto(url, timeout=PAGE_TIMEOUT_MS)
                page.fill(fields_.query, query.value)
                page.click(fields_.submit)
                page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                found = []
                for item in page.query_selector_all(fields_.item)[:MAX_RESULTS]:
                    title_el = (
                        item.query_selector(fields_.item_title) if fields_.item_title else item
                    )
                    link_el = item.query_selector(fields_.item_link)
                    title = title_el.inner_text() if title_el else ""
                    href = link_el.get_attribute("href") if link_el else ""
                    found.append((str(title)[:200], str(href or "")))
                return found
            finally:
                page.close()

        try:
            rows = context.call(drive)
        except Exception:
            logger.warning("G-Knowledge 검색 자동화 실패")
            raise KnowledgeUnavailableError("G-Knowledge 검색에 실패했습니다") from None
        return [
            KnowledgeHit(title, link) for title, href in rows if (link := self._same_site(href))
        ]
