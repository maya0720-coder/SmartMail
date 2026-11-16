"""요청자 구분과 외부 회사명 판정(결정기록 0027). 순수 로직만 둔다.

- 발신 주소에서 도메인만 뽑아 소문자·IDNA(ASCII)로 정규화한다. 주소·이름은 저장하지 않는다.
- 내부 도메인이면 기존 규칙(이름에서 부서 추출), 개인 메일 도메인이면 '외부(개인 메일)',
  그 밖은 외부 회사로 보고 회사명을 매핑 → 캐시 → AI 순서로 찾는다(AI 질의는 백그라운드).
- 하위 도메인은 상위 도메인으로도 찾는다(mail.example.co.kr → example.co.kr).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

KIND_INTERNAL: Final = "internal"
KIND_EXTERNAL: Final = "external"
KIND_FREEMAIL: Final = "freemail"

SOURCE_RULE: Final = "rule"
SOURCE_AI: Final = "ai"
SOURCE_USER: Final = "user"
COMPANY_SOURCES: Final = (SOURCE_RULE, SOURCE_AI, SOURCE_USER)

MAX_DOMAIN_CHARS: Final = 253
MAX_COMPANY_CHARS: Final = 50
FREEMAIL_LABEL: Final = "외부(개인 메일)"

_LABEL: Final = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_URL_LIKE: Final = re.compile(r"https?://|www\.|[\w.-]+\.(?:com|net|org|kr|co|io)\b", re.I)
_NUMBERS_ONLY: Final = re.compile(r"^[\d\s.,:/()+-]+$")
_UNKNOWN_WORDS: Final = ("모름", "알 수 없", "알수없", "unknown", "없음", "확인 불가")


def normalize_domain(value: str) -> str | None:
    """주소 또는 도메인에서 도메인만 뽑아 정규화한다. 형식이 틀리면 None."""
    text = value.strip().strip("<>").rstrip(".")
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    text = text.strip().rstrip(".").lower()
    if not text or len(text) > MAX_DOMAIN_CHARS:
        return None
    try:
        ascii_domain = text.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = ascii_domain.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        return None
    if labels[-1].isdigit():
        return None  # IP 주소 형태는 회사 도메인이 아니다
    return ascii_domain


def parent_domains(domain: str) -> list[str]:
    """자신부터 상위 도메인 순서(최소 2단계까지). 예: a.b.co.kr → [a.b.co.kr, b.co.kr, co.kr]."""
    labels = domain.split(".")
    return [".".join(labels[index:]) for index in range(len(labels) - 1)]


def matches_domain(domain: str, candidates: Iterable[str]) -> bool:
    wanted = set(candidates)
    return any(item in wanted for item in parent_domains(domain))


def valid_company_name(value: str | None) -> str | None:
    """AI·사용자 입력 회사명 검사: 50자 이하, URL·메일 주소·숫자만인 값·'모름'은 버린다."""
    if value is None:
        return None
    text = " ".join(value.split()).strip().strip("\"'“”`")
    if not text or len(text) > MAX_COMPANY_CHARS:
        return None
    if "@" in text or _URL_LIKE.search(text) or _NUMBERS_ONLY.match(text):
        return None
    if any(word in text.lower() for word in _UNKNOWN_WORDS):
        return None
    if any(not ch.isprintable() for ch in text) or any(ch in text for ch in "<>{}[]"):
        return None
    return text


@dataclass(frozen=True, slots=True)
class Requester:
    """요청자 구분 결과. domain은 외부 회사일 때만 둔다(개인 메일 도메인은 저장하지 않음)."""

    kind: str
    domain: str | None = None


class RequesterClassifier:
    """발신 도메인으로 내부·외부 회사·개인 메일을 나눈다."""

    def __init__(self, internal_domains: Iterable[str], free_mail: Iterable[str]) -> None:
        self._internal = {d for raw in internal_domains if (d := normalize_domain(raw))}
        self._free = {d for raw in free_mail if (d := normalize_domain(raw))}

    @property
    def enabled(self) -> bool:
        """내부 도메인을 모르면 나누지 않는다(기존 규칙 그대로)."""
        return bool(self._internal)

    def classify(self, sender_address: str) -> Requester | None:
        domain = normalize_domain(sender_address) if sender_address else None
        if not self.enabled or domain is None:
            return None
        if matches_domain(domain, self._internal):
            return Requester(KIND_INTERNAL)
        if matches_domain(domain, self._free):
            return Requester(KIND_FREEMAIL)
        return Requester(KIND_EXTERNAL, domain)


@dataclass(frozen=True, slots=True)
class CompanyName:
    company: str
    source: str  # rule | ai | user


class CompanyDirectory(Protocol):
    """외부 회사 도메인 → 회사명 캐시(저장소 구현은 storage/)."""

    def get(self, domain: str) -> CompanyName | None: ...

    def save(self, domain: str, company: str, source: str) -> None: ...

    def delete(self, domain: str) -> bool: ...

    def list_all(self) -> list[tuple[str, CompanyName]]: ...


class CompanyResolver:
    """매핑(규칙 파일) → 캐시 순서로 회사명을 찾는다. 사용자 확정값은 규칙보다 앞선다."""

    def __init__(self, rules: Mapping[str, str], cache: CompanyDirectory) -> None:
        self._rules = {
            d: name
            for raw, value in rules.items()
            if (d := normalize_domain(raw)) and (name := valid_company_name(value))
        }
        self._cache = cache

    def lookup(self, domain: str) -> CompanyName | None:
        for candidate in parent_domains(domain):
            cached = self._cache.get(candidate)
            if cached is not None and cached.source == SOURCE_USER:
                return cached
            if candidate in self._rules:
                return CompanyName(self._rules[candidate], SOURCE_RULE)
            if cached is not None:
                return cached
        return None

    def label(self, kind: str | None, domain: str | None) -> tuple[str | None, str | None]:
        """화면 표시값과 출처. (표시값, 출처: rule|ai|user|domain|freemail) 또는 (None, None)."""
        if kind == KIND_FREEMAIL:
            return FREEMAIL_LABEL, "freemail"
        if kind != KIND_EXTERNAL or not domain:
            return None, None
        found = self.lookup(domain)
        if found is not None:
            return found.company, found.source
        return f"외부({domain})", "domain"

    def confirm(self, domain: str, company: str) -> CompanyName:
        """사용자가 확정한 회사명(같은 도메인 전체에 적용)."""
        normalized = normalize_domain(domain)
        name = valid_company_name(company)
        if normalized is None or name is None:
            raise ValueError("도메인 또는 회사명 형식이 올바르지 않습니다")
        self._cache.save(normalized, name, SOURCE_USER)
        return CompanyName(name, SOURCE_USER)

    def needs_ai(self, domain: str) -> bool:
        return self.lookup(domain) is None
