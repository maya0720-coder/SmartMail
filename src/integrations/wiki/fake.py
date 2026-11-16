"""테스트·데모용 가짜 위키. 메모리에만 페이지를 두고 버전 충돌을 흉내 낸다."""

from __future__ import annotations

from dataclasses import replace

from domain.weekly.service import WikiConflictError, WikiPage, WikiUnavailableError
from security.links import safe_link

FAKE_BASE = "https://wiki.example.invalid/pages/"


class FakeWiki:
    def __init__(self) -> None:
        self.pages: dict[str, WikiPage] = {}  # 제목 → 페이지

    def get_page(self, space_key: str, title: str) -> WikiPage | None:
        return self.pages.get(title)

    def get_page_by_link(self, link: str) -> WikiPage | None:
        """가짜 위키 기준 주소의 링크만 읽는다(페이지 번호로 조회). 그 밖의 링크는 None."""
        if safe_link(link, [FAKE_BASE]) is None or not link.startswith(FAKE_BASE):
            raise WikiUnavailableError("위키 기준 주소와 다른 링크라 조회하지 않습니다")
        page_id = link[len(FAKE_BASE) :].split("?", 1)[0].strip("/")
        return next((p for p in self.pages.values() if p.page_id == page_id), None)

    def create_page(self, space_key: str, title: str, body: str) -> WikiPage:
        if title in self.pages:
            raise WikiConflictError("같은 제목의 페이지가 이미 있습니다")
        page_id = str(len(self.pages) + 1)
        page = WikiPage(page_id, title, 1, body, FAKE_BASE + page_id + "?v=1")
        self.pages[title] = page
        return page

    def update_page(self, page: WikiPage, body: str) -> WikiPage:
        current = self.pages.get(page.title)
        if current is None or current.version != page.version:
            raise WikiConflictError("페이지 버전이 바뀌었습니다")
        version = current.version + 1
        updated = replace(
            current, version=version, body=body, link=f"{FAKE_BASE}{current.page_id}?v={version}"
        )
        self.pages[page.title] = updated
        return updated

    def edit_by_other(self, title: str, body: str) -> None:
        """다른 팀원이 페이지를 고친 상황(버전 증가)."""
        current = self.pages[title]
        self.pages[title] = replace(current, version=current.version + 1, body=body)
