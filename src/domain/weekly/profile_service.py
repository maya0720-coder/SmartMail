"""주간보고 양식 프로필 서비스(N4, WIKI-02, 결정기록 0037).

- 가져오기: 위키 로그인 세션으로 페이지 본문을 읽거나(브라우저 읽기),
  사용자가 붙여넣은 HTML을 받는다.
  어느 쪽이든 **내 영역**만 구조로 바꿔 저장한다(값·다른 팀원 영역·이름은 저장하지 않음).
  - 위키에서 읽을 때 내 영역 머리글(h2, 본인 이름)을 못 찾으면 거부한다(다른 팀원 영역 보호).
  - 붙여넣기에서 내 영역 머리글이 없으면 붙여넣은 전체를 내 영역으로 본다. 이때 h1·h2 머리글은
    양식 키워드(실적·계획·이슈)와 맞을 때만 남긴다(이름일 수 있는 머리글은 버림).
- 양식 바뀜 확인: 위키 세션이 **이미 있을 때만** 다시 읽어 구조 지문을 비교한다
  (로그인 창 없음, 0034).
- 상태(작업 센터용)는 메모리에만 둔다.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from domain.errors import UserInputError
from domain.models import Task
from domain.ports import Clock
from domain.weekly.form import FormRules, classify_heading
from domain.weekly.links import Platform, ReferenceLink
from domain.weekly.sections import find_own_section
from domain.weekly.template_profile import (
    Bindings,
    ProfileBlock,
    bindings_from_json,
    blocks_from_json,
    blocks_json,
    extract_blocks,
    rebind,
    render_profile,
    structure_hash,
    suggest_bindings,
    validate_bindings,
)

NOT_WIKI_LINK = "위키 링크만 위키에서 읽을 수 있습니다. 슬랙·기타 웹은 '양식 붙여넣기'를 써 주세요"
OWN_NOT_FOUND = (
    "위키 페이지에서 내 영역 머리글을 찾지 못했습니다. "
    "내 영역만 복사해 '양식 붙여넣기'로 등록해 주세요"
)
LINK_NOT_FOUND = "선택한 참고 링크를 찾을 수 없습니다"


class WikiReader(Protocol):
    def has_session(self) -> bool:
        """위키 로그인 세션이 지금 있는지(새로 로그인하지 않음)."""
        ...

    def read_page_html(self, link: str) -> str:
        """위키 페이지 본문 영역 HTML. 위키 기준 주소 밖이면 거부한다."""
        ...


class LinkLookup(Protocol):
    def get(self, link_id: int) -> ReferenceLink | None: ...


@dataclass(frozen=True, slots=True)
class StoredProfile:
    link_id: int
    name: str
    blocks_json: str
    bindings_json: str
    source_hash: str
    source: str  # wiki | paste
    fetched_at: datetime


class ProfileStore(Protocol):
    def save(self, profile: StoredProfile) -> None: ...

    def update_bindings(self, link_id: int, bindings_json: str) -> bool: ...

    def get(self, link_id: int) -> StoredProfile | None: ...

    def linked_ids(self) -> set[int]: ...

    def delete(self, link_id: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class Profile:
    link_id: int
    name: str
    blocks: list[ProfileBlock]
    bindings: Bindings
    source_hash: str
    source: str
    fetched_at: datetime

    def as_json(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "name": self.name,
            "blocks": [b.as_json() for b in self.blocks],
            "bindings": self.bindings.as_json(),
            "source_hash": self.source_hash,
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat(),
        }


@dataclass(slots=True)
class WikiReadStatus:
    """작업 센터에 보일 위키 양식 읽기 상태(메모리). reason은 코드 값만."""

    state: str = ""  # running | done | failed (빈 값: 아직 없음)
    at: datetime | None = None
    reason: str | None = None
    target: str = ""


def own_region(source: str, own_heading: str, *, strict: bool) -> tuple[str, bool]:
    """내 영역 HTML과 '내 영역 머리글을 찾았는지'. strict면 못 찾을 때 거부한다."""
    span = find_own_section(source, own_heading)
    if span is None:
        if strict:
            raise UserInputError(OWN_NOT_FOUND)
        return source, False
    region = source[span.start : span.end]
    close = region.lower().find("</h2>")
    return (region[close + len("</h2>") :] if close >= 0 else region), True


class WeeklyProfileService:
    def __init__(
        self,
        store: ProfileStore,
        links: LinkLookup,
        wiki: WikiReader,
        clock: Clock,
        rules: FormRules,
        own_heading: Callable[[], str],
    ) -> None:
        self._store = store
        self._links = links
        self._wiki = wiki
        self._clock = clock
        self._rules = rules
        self._own_heading = own_heading
        self._lock = threading.Lock()
        self.status = WikiReadStatus()

    # ---- 조회 ----
    def get(self, link_id: int) -> Profile | None:
        stored = self._store.get(link_id)
        if stored is None:
            return None
        blocks = blocks_from_json(stored.blocks_json)
        return Profile(
            stored.link_id,
            stored.name,
            blocks,
            bindings_from_json(blocks, stored.bindings_json),
            stored.source_hash,
            stored.source,
            stored.fetched_at,
        )

    def linked_ids(self) -> set[int]:
        return self._store.linked_ids()

    def _link(self, link_id: int) -> ReferenceLink:
        link = self._links.get(link_id)
        if link is None:
            raise UserInputError(LINK_NOT_FOUND)
        return link

    # ---- 가져오기 ----
    def _keep_heading(self, level: int, text: str) -> bool:
        return level >= 3 or classify_heading(text, self._rules) is not None

    def _save(self, link: ReferenceLink, blocks: list[ProfileBlock], source: str) -> Profile:
        old = self.get(link.id)
        bindings = (
            rebind(old.blocks, old.bindings, blocks)
            if old is not None
            else suggest_bindings(blocks, self._rules)
        )
        profile = Profile(
            link.id,
            link.name or link.url,
            blocks,
            bindings,
            structure_hash(blocks),
            source,
            self._clock.now(),
        )
        self._store.save(self._stored(profile))
        return profile

    @staticmethod
    def _stored(profile: Profile) -> StoredProfile:
        return StoredProfile(
            profile.link_id,
            profile.name[:120],
            blocks_json(profile.blocks),
            json.dumps(profile.bindings.as_json(), ensure_ascii=False),
            profile.source_hash,
            profile.source,
            profile.fetched_at,
        )

    def _read_wiki(self, link: ReferenceLink) -> list[ProfileBlock]:
        """위키 페이지를 읽어 내 영역 구조를 만든다. 실패는 상태에 코드로 남기고 다시 낸다."""
        with self._lock:
            self.status = WikiReadStatus(
                "running", self._clock.now(), None, link.name or "위키 양식"
            )
        try:
            source = self._wiki.read_page_html(link.url)
            region, _found = own_region(source, self._own_heading(), strict=True)
            blocks = extract_blocks(region)
        except Exception as exc:
            reason = "login_required" if "로그인" in str(exc) else "failed"
            if isinstance(exc, UserInputError):
                reason = "not_found" if str(exc) == OWN_NOT_FOUND else "invalid"
            with self._lock:
                self.status = WikiReadStatus(
                    "failed", self._clock.now(), reason, self.status.target
                )
            raise
        with self._lock:
            self.status = WikiReadStatus("done", self._clock.now(), None, self.status.target)
        return blocks

    def import_from_wiki(self, link_id: int) -> Profile:
        link = self._link(link_id)
        if link.platform is not Platform.WIKI:
            raise UserInputError(NOT_WIKI_LINK)
        return self._save(link, self._read_wiki(link), "wiki")

    def import_from_paste(self, link_id: int, source: str) -> Profile:
        link = self._link(link_id)
        region, found = own_region(source, self._own_heading(), strict=False)
        keep = None if found else self._keep_heading
        return self._save(link, extract_blocks(region, keep_heading=keep), "paste")

    # ---- 칸 연결 ----
    def save_bindings(self, link_id: int, raw: Mapping[str, Any]) -> Profile:
        profile = self.get(link_id)
        if profile is None:
            raise UserInputError("등록된 양식이 없습니다")
        bindings = validate_bindings(profile.blocks, raw)
        updated = Profile(
            profile.link_id,
            profile.name,
            profile.blocks,
            bindings,
            profile.source_hash,
            profile.source,
            profile.fetched_at,
        )
        self._store.update_bindings(link_id, self._stored(updated).bindings_json)
        return updated

    def delete(self, link_id: int) -> bool:
        return self._store.delete(link_id)

    # ---- 양식 바뀜 확인 ----
    def check(self, link_id: int) -> tuple[Profile | None, bool, bool]:
        """(프로필, 바뀜, 확인함). 위키 세션이 없으면 확인하지 않고 저장 프로필을 쓴다."""
        profile = self.get(link_id)
        if profile is None or profile.source != "wiki" or not self._wiki.has_session():
            return profile, False, False
        link = self._links.get(link_id)
        if link is None:
            return profile, False, False
        try:
            blocks = self._read_wiki(link)
        except Exception:  # noqa: BLE001 - 확인 실패는 저장 프로필로 계속(상태에 사유 코드 기록)
            return profile, False, False
        if structure_hash(blocks) == profile.source_hash:
            return profile, False, True
        return self._save(link, blocks, "wiki"), True, True

    # ---- 초안 ----
    def render(
        self,
        profile: Profile,
        sections: Mapping[str, list[Task]],
        item_text: Callable[[Task], str],
    ) -> str:
        return render_profile(profile.blocks, profile.bindings, sections, item_text)
