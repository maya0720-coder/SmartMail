"""주간보고 참고 링크: 검증·플랫폼 판별(순수 함수)과 등록 서비스(WIKI-01).

- https만 허용한다. 사용자 정보·제어 문자·비ASCII 문자·과도한 길이는 거부한다.
- 플랫폼은 호스트가 정확히 일치하거나 `.slack.com`으로 끝날 때만 슬랙으로 본다
  (slack.com.evil.example은 기타 웹). Teams·메일(Outlook 웹)·Markdown(노션·깃) 호스트도 같은
  규칙으로 판별한다(결정기록 0041).
- 서버는 이 링크를 직접 가져오지 않는다(SSRF 회피). 위키 본문은 위키 어댑터로만 읽는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from domain.errors import UserInputError
from domain.weekly.form import parse_outline
from security.masking import mask

MAX_LINKS = 10  # 등록 가능한 참고 링크 수
MAX_URL_CHARS = 500
MAX_NAME_CHARS = 40
SLACK_DOMAIN = "slack.com"
# 호스트 정확 일치 또는 하위 도메인(결정기록 0041)
TEAMS_DOMAINS = ("teams.microsoft.com", "teams.live.com")
MAIL_DOMAINS = ("outlook.office.com", "outlook.office365.com", "outlook.live.com")
MARKDOWN_DOMAINS = ("notion.so", "notion.site", "github.com", "gitlab.com")

_URL_CHARS = re.compile(r"^[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+$")
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(\.{_LABEL})*$")
_WIKI_PATHS = (
    re.compile(r"^/display/[^/]+"),
    re.compile(r"^/pages/viewpage\.action$"),
    re.compile(r"^/wiki/spaces/[^/]+"),
    re.compile(r"^/spaces/[^/]+/pages/"),
)


class Platform(StrEnum):
    SLACK = "slack"
    WIKI = "wiki"
    WEB = "web"
    TEAMS = "teams"
    MAIL = "mail"
    MARKDOWN = "markdown"


PLATFORM_LABELS = {
    Platform.SLACK: "슬랙",
    Platform.WIKI: "위키",
    Platform.WEB: "기타 웹",
    Platform.TEAMS: "Teams",
    Platform.MAIL: "메일",
    Platform.MARKDOWN: "Markdown",
}


@dataclass(frozen=True, slots=True)
class ReferenceLink:
    id: int
    url: str
    name: str
    platform: Platform
    outline: str  # 사용자가 붙여 넣은 양식 목차(머리글 줄, 마스킹됨). 없으면 빈 문자열


class LinkStore(Protocol):
    def add(self, url: str, name: str, platform: str, outline: str) -> ReferenceLink: ...

    def list_all(self) -> list[ReferenceLink]: ...

    def get(self, link_id: int) -> ReferenceLink | None: ...

    def delete(self, link_id: int) -> bool: ...


def normalize_url(url: str) -> str:
    """검증하고 조각(#...)을 뗀 https 주소를 돌려준다. 잘못되면 UserInputError."""
    text = url.strip()
    if not text or len(text) > MAX_URL_CHARS or not _URL_CHARS.fullmatch(text):
        raise UserInputError("링크 형식이 올바르지 않습니다(영문·숫자 주소, 최대 500자)")
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").rstrip(".")
        port = parts.port
    except ValueError:
        raise UserInputError("링크 형식이 올바르지 않습니다") from None
    if parts.scheme != "https":
        raise UserInputError("https 링크만 등록할 수 있습니다")
    if "@" in parts.netloc:
        raise UserInputError("사용자 정보가 포함된 링크는 등록할 수 없습니다")
    if not _HOST.fullmatch(host):
        raise UserInputError("링크의 호스트가 올바르지 않습니다")
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit(("https", netloc, parts.path, parts.query, ""))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").rstrip(".").lower()


def _under(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def detect_platform(url: str, wiki_base_url: str = "") -> Platform:
    """URL 패턴으로 플랫폼을 판별한다(normalize_url을 통과한 주소 기준)."""
    host = _host(url)
    if host == SLACK_DOMAIN or host.endswith("." + SLACK_DOMAIN):
        return Platform.SLACK
    for domains, platform in (
        (TEAMS_DOMAINS, Platform.TEAMS),
        (MAIL_DOMAINS, Platform.MAIL),
        (MARKDOWN_DOMAINS, Platform.MARKDOWN),
    ):
        if _under(host, domains):
            return platform
    wiki_host = _host(wiki_base_url) if wiki_base_url else ""
    if wiki_host and host == wiki_host:
        return Platform.WIKI
    path = urlsplit(url).path
    if any(pattern.match(path) for pattern in _WIKI_PATHS):
        return Platform.WIKI
    return Platform.WEB


def normalize_name(name: str) -> str:
    text = " ".join(name.split())
    if len(text) > MAX_NAME_CHARS:
        raise UserInputError(f"이름은 {MAX_NAME_CHARS}자 이하로 입력해 주세요")
    return mask(text).value if text else ""


class ReferenceLinkService:
    def __init__(self, store: LinkStore, wiki_base_url: str) -> None:
        self._store = store
        self._wiki_base = wiki_base_url

    def add(self, url: str, name: str = "", outline: str = "") -> ReferenceLink:
        clean = normalize_url(url)
        headings = parse_outline(outline)
        existing = self._store.list_all()
        if len(existing) >= MAX_LINKS:
            raise UserInputError(f"참고 링크는 최대 {MAX_LINKS}개까지 등록할 수 있습니다")
        if any(link.url == clean for link in existing):
            raise UserInputError("이미 등록된 링크입니다")
        platform = detect_platform(clean, self._wiki_base)
        return self._store.add(clean, normalize_name(name), platform.value, "\n".join(headings))

    def list_all(self) -> list[ReferenceLink]:
        return self._store.list_all()

    def get(self, link_id: int) -> ReferenceLink | None:
        return self._store.get(link_id)

    def delete(self, link_id: int) -> bool:
        return self._store.delete(link_id)
