"""G-Knowledge 브라우저 자동화(GK-03·04·05, SRCH-02).

- 로그인한 비저장 브라우저 컨텍스트(04단계 `EphemeralContext`)로만 동작한다.
- 화면 필드 선택자는 `config/rules/gknowledge_fields.toml`에서 읽는다. 비어 있으면(확인 필요)
  자동화를 하지 않고 `KnowledgeUnavailableError`로 안내한다(추측 구현 금지).
- 등록 화면 항목은 제목·내용·첨부다(결정기록 0008 G-3). 태그 칸이 없으면 태그를 본문 끝에 붙이고,
  첨부는 자동화하지 않는다(메일 첨부를 저장·전송하지 않음).
- 등록 화면은 2단계다(결정기록 0010·0032): 제목·본문 입력 → 'AI 분석' → 분석 결과 칸이
  보이면 '등록요청'. 분석 결과는 G-Knowledge 값 그대로 두고, 본문 입력은 보내기 전에 확인한다.
- 등록 결과는 '결과 링크 요소', '결과 주소 형식(result_path)과 맞는 주소', '접수 안내 요소
  (result_message)' 중 하나로만 확인한다. 실제 화면은 주소가 그대로이고 접수 안내만 뜬다(0032).
  확인하지 못하면 중복 등록을 막기 위해 다시 보내지 않고 사용자에게 직접 확인을 요청한다(G-4·G-6).
- 검색 경로에 `{query}`가 있으면 주소로 검색한다(입력칸 자동화보다 화면 변경에 강함, G-5).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote, urljoin, urlsplit

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
# G-Knowledge 'AI 분석' 결과를 기다리는 시간(ms). 넘으면 '등록요청' 전이므로 보내지 않고 멈춘다.
ANALYZE_WAIT_MS: Final = 60_000
# 검색 결과 대기(ms). 시간 안에 결과 항목이 없으면 '결과 없음'으로 본다.
SEARCH_WAIT_MS: Final = 10_000
# 등록 포기 시각(초). 브라우저 대기열에서 이 시간 뒤에 꺼내지면 등록하지 않는다.
REGISTER_TIMEOUT_SECONDS: Final = 150.0  # 화면 열기 30초 + AI 분석 60초 + 결과 확인 30초 + 여유
CALL_MARGIN_SECONDS: Final = 10.0
MAX_RESULT_PATTERN_CHARS: Final = 200
QUERY_PLACEHOLDER: Final = "{query}"
UNCERTAIN_RESULT: Final = (
    "등록 결과를 확인하지 못했습니다. 중복 등록을 막기 위해 G-Knowledge에서 등록 여부를 "
    "먼저 확인해 주세요"
)
MAX_RESULTS: Final = 10
NOT_CONFIRMED: Final = (
    "G-Knowledge 화면 필드가 확인되지 않아 자동 등록·검색을 할 수 없습니다(확인 필요)"
)
LOGIN_REQUIRED: Final = "G-Knowledge 로그인이 필요합니다"
NOT_SENT: Final = "G-Knowledge가 바빠 등록하지 않았습니다. 잠시 후 다시 시도해 주세요"
ANALYZE_FAILED: Final = (
    "G-Knowledge AI 분석이 끝나지 않아 등록하지 않았습니다. 잠시 후 다시 시도해 주세요"
)
BODY_NOT_ENTERED: Final = "G-Knowledge 본문 입력을 확인하지 못해 등록하지 않았습니다"
# 분석 완료 요소에 글자가 들어왔는지 보는 스크립트(입력칸이면 value, 아니면 글자).
# 결과 칸이 먼저 보이고 값이 나중에 채워지는 경우 빈 결과로 등록요청하지 않게 한다.
FILLED_SCRIPT: Final = (
    "s => { const e = document.querySelector(s);"
    " return !!e && String(('value' in e) ? e.value : e.textContent).trim().length > 0; }"
)


def _compact(text: str) -> str:
    # 편집기(Quill)가 줄바꿈·공백을 바꿔 담을 수 있어 공백을 빼고 비교한다
    return "".join(text.split())


@dataclass(frozen=True, slots=True)
class RegisterFields:
    path: str
    title: str
    body: str
    tags: str
    submit: str
    result_link: str
    result_path: str
    # 2단계 등록 화면(결정기록 0010·0032):
    # 작성 → 'AI 분석' → 분석 결과 칸이 보이면 '등록요청'(submit).
    # analyze가 비어 있으면 1단계(작성 → submit)로 동작한다.
    analyze: str = ""
    analysis_ready: str = ""  # 분석이 끝났다고 볼 요소(보이고 글자가 들어올 때까지 기다림)
    # 등록요청 뒤 접수 안내 요소. 주소가 바뀌지 않는 화면에서 결과를 확인한다(결정기록 0032).
    result_message: str = ""

    def missing(self) -> list[str]:
        """자동 등록에 필요한데 비어 있는 설정 이름(화면 안내용, 값은 담지 않음)."""
        names = [n for n in ("path", "title", "body", "submit") if not getattr(self, n)]
        if self.analyze and not self.analysis_ready:
            names.append("analysis_ready")
        if not (self.result_link or self.result_path or self.result_message):
            # 결과 확인 방법이 있어야 등록한다
            names.append("result_message(또는 result_path·result_link)")
        return names

    @property
    def ready(self) -> bool:
        return not self.missing()


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
        by_url = QUERY_PLACEHOLDER in self.path
        return all((self.path, self.item, self.item_link)) and (
            by_url or bool(self.query and self.submit)
        )


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
    register: RegisterFields = _section(data, "register", RegisterFields)
    if register.result_path:
        if len(register.result_path) > MAX_RESULT_PATTERN_CHARS:
            raise ConfigError("gknowledge_fields: result_path가 너무 깁니다")
        try:
            re.compile(register.result_path)
        except re.error:
            raise ConfigError("gknowledge_fields: result_path 정규식 오류") from None
    return register, _section(data, "search", SearchFields)


def _is_timeout(exc: BaseException) -> bool:
    # Playwright의 TimeoutError도 이름이 같다(연동 라이브러리를 직접 가져오지 않음)
    return isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError"


class BrowserKnowledgeStore:
    name = "g-knowledge"

    def __init__(
        self,
        base_url: str,
        sessions: SessionStore,
        register_fields: RegisterFields,
        search_fields: SearchFields,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base_url.rstrip("/") + "/"
        self._sessions = sessions
        self._register = register_fields
        self._search = search_fields
        self._now = now

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
        # https + 같은 호스트·포트만 허용
        if safe_link(url, [self._base]) is None:
            raise KnowledgeUnavailableError("G-Knowledge 주소 설정이 올바르지 않습니다")
        return url

    def _same_site(self, link: str) -> str:
        # https + 같은 호스트·포트만 허용(javascript:, http 다운그레이드, 다른 포트 거부)
        return safe_link(link, [self._base], base_for_relative=self._base) or ""

    def register(self, draft: MaskedDraft) -> RegisterResult:
        fields_ = self._register
        if not fields_.ready:
            # 어떤 설정이 비었는지 이름만 알린다(gknowledge_fields.toml [register])
            missing = ", ".join(fields_.missing())
            raise KnowledgeUnavailableError(f"{NOT_CONFIRMED} — 미설정: {missing}")
        context = self._context()
        url = self._url(fields_.path)
        register_path = urlsplit(url).path.rstrip("/")
        tags = ", ".join(t.value for t in draft.tags)
        body = draft.body.value
        if tags and not fields_.tags:
            body = f"{body}\n\n태그: {tags}"  # 태그 칸이 없는 화면(G-3)

        def is_result_page(current: str) -> bool:
            # 같은 사이트이고, 등록 화면이 아니며, 결과 주소 형식과 맞아야 성공으로 본다
            # (로그인·오류·목록 화면으로 간 경우를 성공으로 오인하지 않게)
            if self._same_site(current) != current:
                return False
            path = urlsplit(current).path
            if path.rstrip("/") == register_path:
                return False
            return re.fullmatch(fields_.result_path, path) is not None

        deadline = self._now() + REGISTER_TIMEOUT_SECONDS
        submitted = threading.Event()

        def drive(raw: Any) -> str:
            if self._now() >= deadline:
                raise KnowledgeUnavailableError(NOT_SENT)
            page = raw.new_page()
            try:
                page.goto(url, timeout=PAGE_TIMEOUT_MS)
                page.fill(fields_.title, draft.title.value)
                page.fill(fields_.body, body)
                if _compact(str(page.inner_text(fields_.body))) != _compact(body):
                    raise KnowledgeUnavailableError(BODY_NOT_ENTERED)  # 보내기 전이므로 안전
                if fields_.tags and tags:
                    page.fill(fields_.tags, tags)
                if fields_.analyze:
                    # 2단계 화면: 'AI 분석' 결과는 G-Knowledge가 채운 그대로 둔다(사용자 결정)
                    page.click(fields_.analyze)
                    started = self._now()
                    try:
                        page.wait_for_selector(fields_.analysis_ready, timeout=ANALYZE_WAIT_MS)
                        # 칸이 보여도 값이 비어 있으면 아직 분석 중으로 본다(남은 시간만 대기)
                        left_ms = ANALYZE_WAIT_MS - int((self._now() - started) * 1000)
                        if left_ms <= 0:
                            raise TimeoutError("분석 대기 시간 초과")
                        page.wait_for_function(
                            FILLED_SCRIPT, arg=fields_.analysis_ready, timeout=left_ms
                        )
                    except Exception:
                        raise KnowledgeUnavailableError(ANALYZE_FAILED) from None
                if self._now() >= deadline:  # 보내기 직전 마지막 확인
                    raise KnowledgeUnavailableError(NOT_SENT)
                submitted.set()  # 여기부터는 등록됐을 수 있음
                try:
                    page.click(fields_.submit)
                    if fields_.result_link:
                        page.wait_for_selector(fields_.result_link, timeout=PAGE_TIMEOUT_MS)
                        return str(page.get_attribute(fields_.result_link, "href") or "")
                    if fields_.result_message:
                        page.wait_for_selector(fields_.result_message, timeout=PAGE_TIMEOUT_MS)
                        # 접수 화면에는 지식 링크가 없다(검토 대기). G-Knowledge 첫 화면을 남긴다
                        return self._base
                    page.wait_for_url(is_result_page, timeout=PAGE_TIMEOUT_MS)
                    return str(page.url)
                except Exception:
                    raise KnowledgeUnavailableError(UNCERTAIN_RESULT) from None
            finally:
                page.close()

        try:
            link = context.call(drive, timeout=REGISTER_TIMEOUT_SECONDS + CALL_MARGIN_SECONDS)
        except KnowledgeUnavailableError:
            raise
        except Exception:
            logger.warning("G-Knowledge 등록 자동화 실패")
            if submitted.is_set():
                raise KnowledgeUnavailableError(UNCERTAIN_RESULT) from None
            raise KnowledgeUnavailableError("G-Knowledge 등록에 실패했습니다") from None
        safe = self._same_site(link)
        if not safe:
            raise KnowledgeUnavailableError(UNCERTAIN_RESULT)
        return RegisterResult(safe)

    def search(self, query: MaskedText) -> list[KnowledgeHit]:
        fields_ = self._search
        if not fields_.ready:
            raise KnowledgeUnavailableError(NOT_CONFIRMED)
        context = self._context()
        by_url = QUERY_PLACEHOLDER in fields_.path
        path = fields_.path.replace(QUERY_PLACEHOLDER, quote(query.value, safe=""))
        url = self._url(path)

        def drive(raw: Any) -> list[tuple[str, str]]:
            page = raw.new_page()
            try:
                page.goto(url, timeout=PAGE_TIMEOUT_MS)
                if not by_url:
                    page.fill(fields_.query, query.value)
                    page.click(fields_.submit)
                try:
                    page.wait_for_selector(fields_.item, timeout=SEARCH_WAIT_MS)
                except Exception as exc:
                    if _is_timeout(exc):
                        return []  # 시간 안에 결과 항목이 없으면 결과 없음
                    raise  # 그 밖의 오류는 '결과 없음'으로 숨기지 않는다
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
