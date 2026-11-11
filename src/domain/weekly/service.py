"""주간보고 흐름: 초안 → 편집 → 미리보기 → 승인 → 게시(WIKI-03~06).

승인 없이 게시하는 경로가 없도록, 게시 함수는 `ApprovedReport`만 받는다.
`ApprovedReport`는 `approve()`로만 만들 수 있고,
미리보기 때 받은 지문과 페이지 버전이 일치해야 한다.
게시 내용은 마스킹하고, 본인 영역만 바꾸며, 버전 충돌이면 중단한다.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from domain.errors import UserInputError
from domain.models import TaskEventType
from domain.ports import Clock, EventRecorder
from domain.tasks.service import TaskService
from domain.weekly.report import (
    WeeklyDraft,
    WikiTemplate,
    classify_week,
    render_section,
    replace_own_section,
)
from domain.weekly.sections import current_section_text
from security.masking import mask


class WikiUnavailableError(Exception):
    """위키 사용 불가(API 규격·인증 미확인, 로그인 필요, 오류). 메시지는 사용자 안내용."""


class WikiConflictError(Exception):
    """미리보기 이후 페이지가 바뀜(버전 충돌). 게시를 중단한다."""


@dataclass(frozen=True, slots=True)
class WikiPage:
    page_id: str
    title: str
    version: int
    body: str
    link: str


class WikiApi(Protocol):
    def get_page(self, space_key: str, title: str) -> WikiPage | None: ...

    def create_page(self, space_key: str, title: str, body: str) -> WikiPage: ...

    def update_page(self, page: WikiPage, body: str) -> WikiPage:
        """page.version을 기준으로 갱신한다. 서버 버전이 다르면 WikiConflictError."""
        ...


class PublishLog(Protocol):
    def record(
        self,
        week_start: datetime,
        page: WikiPage,
        approved_at: datetime,
        published_at: datetime,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class Preview:
    title: str
    section_html: str  # 이스케이프된 본인 영역
    digest: str
    base_version: int | None  # 미리보기 때 페이지 버전(없으면 새 페이지)
    nonce: str = ""  # 서버가 발급한 1회용 승인 번호(L-1)
    current_section: str | None = None  # 지금 바뀔 기존 본인 영역(H-2 확인용)


_APPROVAL_KEY = object()


class ApprovedReport:
    """사용자 승인을 거친 보고서. `WeeklyService.approve`로만 만든다."""

    __slots__ = ("approved_at", "draft", "mode", "preview")

    def __init__(
        self,
        draft: WeeklyDraft,
        preview: Preview,
        mode: Literal["create", "update"],
        approved_at: datetime,
        _key: object = None,
    ) -> None:
        if _key is not _APPROVAL_KEY:
            raise TypeError("승인 객체는 approve()로만 만들 수 있습니다")
        self.draft = draft
        self.preview = preview
        self.mode = mode
        self.approved_at = approved_at


def _masked(draft: WeeklyDraft) -> WeeklyDraft:
    return WeeklyDraft(
        draft.week_start,
        [mask(i).value for i in draft.done],
        [mask(i).value for i in draft.planned],
        [mask(i).value for i in draft.issues],
    )


DEFAULT_AUTHOR = "본인"
NONCE_TTL = timedelta(minutes=30)
MAX_PENDING = 20


@dataclass(frozen=True, slots=True)
class _Pending:
    digest: str
    base_version: int | None
    expires_at: datetime


class WeeklyService:
    def __init__(
        self,
        tasks: TaskService,
        wiki: WikiApi,
        events: EventRecorder,
        publish_log: PublishLog,
        clock: Clock,
        template: WikiTemplate,
        author: str,
    ) -> None:
        self._tasks = tasks
        self._wiki = wiki
        self._events = events
        self._log = publish_log
        self._clock = clock
        self.template = template
        self._author = author
        self._pending: dict[str, _Pending] = {}

    def draft(self, moment: datetime | None = None) -> WeeklyDraft:
        result = classify_week(self._tasks.list_tasks(), moment or self._clock.now(), self.template)
        self._events.record(TaskEventType.DRAFT_CREATED)
        return result

    def _page(self, draft: WeeklyDraft) -> WikiPage | None:
        if not self.template.space_key:
            return None
        return self._wiki.get_page(
            self.template.space_key, self.template.page_title(draft.week_start)
        )

    def preview(self, draft: WeeklyDraft) -> Preview:
        """사용자가 편집한 초안을 마스킹해 미리보기를 만든다. 위키를 못 읽으면 새 페이지 기준."""
        draft.validate()
        masked = _masked(draft)
        try:
            page = self._page(masked)
        except WikiUnavailableError:
            page = None
        current = None
        if page is not None:
            current = current_section_text(page.body, self.template.heading(self._author))
        now = self._clock.now()
        self._pending = {k: v for k, v in self._pending.items() if v.expires_at > now}
        while len(self._pending) >= MAX_PENDING:
            self._pending.pop(next(iter(self._pending)))
        nonce = secrets.token_urlsafe(24)
        base_version = None if page is None else page.version
        self._pending[nonce] = _Pending(masked.digest(), base_version, now + NONCE_TTL)
        return Preview(
            title=self.template.page_title(masked.week_start),
            section_html=render_section(masked, self.template, self._author),
            digest=masked.digest(),
            base_version=base_version,
            nonce=nonce,
            current_section=current,
        )

    def approve(
        self,
        draft: WeeklyDraft,
        nonce: str,
        mode: Literal["create", "update"],
    ) -> ApprovedReport:
        """서버가 발급한 미리보기 번호(1회용)와 같은 내용일 때만 승인 객체를 만든다(WIKI-03).

        페이지 버전도 클라이언트 값이 아니라 미리보기 때 서버가 본 값을 쓴다.
        """
        draft.validate()
        pending = self._pending.pop(nonce, None)
        if pending is None or pending.expires_at <= self._clock.now():
            raise UserInputError("미리보기가 만료되었습니다. 다시 미리보기 후 승인해 주세요")
        masked = _masked(draft)
        if not secrets.compare_digest(masked.digest(), pending.digest):
            raise UserInputError(
                "미리보기 이후 내용이 바뀌었습니다. 다시 미리보기 후 승인해 주세요"
            )
        fixed = Preview(
            self.template.page_title(masked.week_start),
            render_section(masked, self.template, self._author),
            pending.digest,
            pending.base_version,
        )
        return ApprovedReport(draft, fixed, mode, self._clock.now(), _APPROVAL_KEY)

    def _check_masked(self, approved: ApprovedReport) -> None:
        """보낼 내용이 마스킹된 초안에서만 만들어졌는지 다시 확인한다(M-3)."""
        masked = _masked(approved.draft)
        for item in masked.done + masked.planned + masked.issues:
            if mask(item).value != item:
                raise UserInputError("마스킹되지 않은 민감정보가 남아 있어 게시하지 않았습니다")
        expected = render_section(masked, self.template, self._author)
        if expected != approved.preview.section_html:
            raise UserInputError("게시 내용 확인에 실패했습니다. 다시 미리보기해 주세요")

    def publish(self, approved: ApprovedReport) -> WikiPage:
        if not isinstance(approved, ApprovedReport):
            raise TypeError("승인된 보고서만 게시할 수 있습니다")
        if not self.template.space_key:
            raise WikiUnavailableError(
                "위키 공간·페이지 양식이 확인되지 않아 게시할 수 없습니다(확인 필요)"
            )
        if self._author.strip() == DEFAULT_AUTHOR:
            # 기본 이름이면 다른 팀원의 같은 이름 영역을 덮어쓸 수 있다(보안 검토 H-2).
            raise WikiUnavailableError(
                "SMARTMAIL_USER_DISPLAY_NAME에 위키에서 쓰는 고유한 이름을 설정한 뒤 게시해 주세요"
            )
        self._check_masked(approved)
        preview = approved.preview
        page = self._wiki.get_page(self.template.space_key, preview.title)
        heading = self.template.heading(self._author)
        if approved.mode == "create":
            if page is not None:
                raise WikiConflictError(
                    "같은 제목의 페이지가 이미 있습니다. '기존 페이지 갱신'을 선택해 주세요"
                )
            result = self._wiki.create_page(
                self.template.space_key, preview.title, preview.section_html
            )
        else:
            if page is None:
                raise WikiConflictError("갱신할 페이지가 없습니다")
            if preview.base_version is None or page.version != preview.base_version:
                raise WikiConflictError(
                    "미리보기 이후 다른 사람이 페이지를 고쳤습니다. 다시 확인해 주세요"
                )
            body = replace_own_section(page.body, heading, preview.section_html)
            result = self._wiki.update_page(page, body)
        now = self._clock.now()
        self._log.record(approved.draft.week_start, result, approved.approved_at, now)
        self._events.record(TaskEventType.PUBLISHED)
        return result
