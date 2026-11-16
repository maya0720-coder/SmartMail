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
from domain.weekly.convert import to_platform_text
from domain.weekly.form import (
    FormLayout,
    FormRules,
    build_layout,
    headings_from_html,
    parse_outline,
)
from domain.weekly.links import PLATFORM_LABELS, Platform, ReferenceLink
from domain.weekly.report import (
    WeeklyDraft,
    WikiTemplate,
    classify_week,
    render_section,
    replace_own_section,
    single_line,
    with_layout,
)
from domain.weekly.rich import sanitize, unmasked_text_left
from domain.weekly.sections import current_section_text
from security.links import safe_link
from security.masking import mask


class WikiUnavailableError(Exception):
    """위키 사용 불가(API 규격·인증 미확인, 로그인 필요, 오류). 메시지는 사용자 안내용."""


class WikiLoginRequiredError(WikiUnavailableError):
    """위키 로그인 필요. 로그인 후 다시 시도해야 하므로 클립보드 폴백 대상이 아니다."""


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

    def get_page_by_link(self, link: str) -> WikiPage | None:
        """등록된 참고 링크의 위키 페이지 본문(양식 구조 파싱용). 위키 기준 주소 밖이면 None."""
        ...

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


class PublishCache(Protocol):
    def add(
        self,
        week_start: datetime,
        platform: str,
        method: str,
        link: str | None,
        published_at: datetime,
        snapshot: str,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    """게시 대상. 링크는 등록된 참고 링크(검증 끝난 https)이거나 없음."""

    platform: Platform = Platform.WIKI
    link: str | None = None


@dataclass(frozen=True, slots=True)
class Delivery:
    method: str  # "api" | "clipboard"
    platform: Platform
    text: str  # 대상 플랫폼 형식(클립보드용)
    html: str  # 이스케이프된 본인 영역 HTML(위키 붙여넣기용)
    title: str
    page: WikiPage | None = None
    reason: str | None = None  # 폴백 사유
    cache_id: int | None = None


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


def _masked(draft: WeeklyDraft, link_bases: tuple[str, ...] = ()) -> WeeklyDraft:
    """목록은 항목별 마스킹, 편집기 HTML은 허용 목록 정리 + 글자 마스킹(결정기록 0030)."""
    clean = None
    if draft.html is not None:
        clean = sanitize(draft.html, link_bases)
        if unmasked_text_left(clean):
            raise UserInputError(SPLIT_SENSITIVE)
    return WeeklyDraft(
        draft.week_start,
        [single_line(mask(i).value) for i in draft.done],
        [single_line(mask(i).value) for i in draft.planned],
        [single_line(mask(i).value) for i in draft.issues],
        draft.layout,
        clean,
    )


SPLIT_SENSITIVE = (
    "서식 사이에 걸친 민감정보(전화번호·계좌번호 등)가 있어 받을 수 없습니다. "
    "해당 글자를 고쳐 주세요"
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
        cache: PublishCache | None = None,
        form_rules: FormRules | None = None,
        wiki_base_url: str = "",
        link_bases: tuple[str, ...] = (),
    ) -> None:
        # 편집기 링크로 허용할 사이트(https, 설정의 기준 주소와 출처가 같은 것만)
        self._link_bases = tuple(b for b in (wiki_base_url, *link_bases) if b)
        self._tasks = tasks
        self._wiki = wiki
        self._events = events
        self._log = publish_log
        self._clock = clock
        self.template = template
        self._author = author
        self._pending: dict[str, _Pending] = {}
        self._cache = cache
        self._wiki_base = wiki_base_url
        self._rules = form_rules or FormRules()

    @property
    def own_heading(self) -> str:
        """본인 영역 머리글(h2). 편집기에서 읽기 전용으로 보여 준다."""
        return self.template.heading(self._author)

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
        masked = _masked(draft, self._link_bases)
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
            section_html=render_section(
                masked, with_layout(self.template, masked.layout), self._author
            ),
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
        masked = _masked(draft, self._link_bases)
        if not secrets.compare_digest(masked.digest(), pending.digest):
            raise UserInputError(
                "미리보기 이후 내용이 바뀌었습니다. 다시 미리보기 후 승인해 주세요"
            )
        fixed = Preview(
            self.template.page_title(masked.week_start),
            render_section(masked, with_layout(self.template, masked.layout), self._author),
            pending.digest,
            pending.base_version,
        )
        return ApprovedReport(draft, fixed, mode, self._clock.now(), _APPROVAL_KEY)

    def _check_masked(self, approved: ApprovedReport) -> None:
        """보낼 내용이 마스킹된 초안에서만 만들어졌는지 다시 확인한다(M-3)."""
        masked = _masked(approved.draft, self._link_bases)
        for item in masked.done + masked.planned + masked.issues:
            if mask(item).value != item:
                raise UserInputError("마스킹되지 않은 민감정보가 남아 있어 게시하지 않았습니다")
        if masked.html is not None and (
            unmasked_text_left(masked.html)
            or sanitize(masked.html, self._link_bases) != masked.html
        ):
            raise UserInputError("마스킹되지 않은 민감정보가 남아 있어 게시하지 않았습니다")
        expected = render_section(masked, with_layout(self.template, masked.layout), self._author)
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

    def resolve_layout(self, link: ReferenceLink | None) -> FormLayout:
        """참고 링크 양식의 머리글 구조를 정한다(WIKI-02). 실패하면 기본 양식으로 되돌린다.

        위키 링크는 위키 어댑터로만 읽고(SSRF 회피), 그 외 링크는 사용자가 붙여 넣은 목차만 쓴다.
        """
        tpl = self.template
        defaults = (tpl.heading_done, tpl.heading_planned, tpl.heading_issues)

        def fallback(why: str) -> FormLayout:
            return FormLayout(*defaults, source="default", label=f"기본 양식 ({why})")

        if link is None:
            return fallback("참고 링크 미선택")
        headings: list[str] = []
        why = "양식 정보 없음"
        # 경로 패턴으로 '위키' 배지가 붙어도 서버 조회는 위키 기준 주소와 출처(scheme·호스트·포트)가
        # 정확히 같을 때만 한다. 다르면 조회하지 않고 붙여 넣은 목차로 처리한다(SSRF 방지).
        same_origin = bool(self._wiki_base) and safe_link(link.url, [self._wiki_base]) is not None
        if link.platform is Platform.WIKI and same_origin:
            try:
                page = self._wiki.get_page_by_link(link.url)
                if page is not None:
                    headings = headings_from_html(page.body)
            except WikiUnavailableError as exc:
                why = str(exc)
        if not headings and link.outline:
            headings = parse_outline(link.outline)
        label = f"{PLATFORM_LABELS[link.platform]} 양식 {link.name or link.url}"
        layout = build_layout(headings, defaults, self._rules, label)
        if layout is None:
            return fallback("매핑되는 머리글 없음" if headings else why)
        return layout

    def convert(self, draft: WeeklyDraft, platform: Platform) -> tuple[str, str]:
        """마스킹한 초안을 대상 플랫폼 형식(텍스트, 위키용 HTML)으로 바꾼다. 게시는 하지 않는다."""
        draft.validate()
        masked = _masked(draft, self._link_bases)
        text = to_platform_text(masked, self.template, self._author, platform)
        html = render_section(masked, with_layout(self.template, masked.layout), self._author)
        return text, html

    def deliver(
        self, approved: ApprovedReport, target: DeliveryTarget, *, allow_fallback: bool
    ) -> Delivery:
        """승인된 보고서를 게시한다. 위키 API가 되면 자동 게시, 아니면 클립보드 폴백(WIKI-06).

        폴백이면 호출한 화면이 변환본을 클립보드에 넣고 대상 링크를 연다.
        어느 쪽이든 게시 캐시에 남긴다.
        """
        if not isinstance(approved, ApprovedReport):
            raise TypeError("승인된 보고서만 게시할 수 있습니다")
        page: WikiPage | None = None
        reason: str | None = None
        if target.platform is Platform.WIKI:
            try:
                page = self.publish(approved)
            except WikiLoginRequiredError:
                raise
            except WikiUnavailableError as exc:
                if not allow_fallback:
                    raise
                reason = str(exc)
        elif allow_fallback:
            reason = "슬랙·기타 웹은 API 연동이 없어 복사 후 붙여넣는 방식으로 처리합니다"
        else:
            raise WikiUnavailableError("슬랙·기타 웹은 자동 게시를 지원하지 않습니다")
        masked = _masked(approved.draft, self._link_bases)
        if page is None:
            self._check_masked(approved)
            self._events.record(TaskEventType.PUBLISHED)
        text = to_platform_text(masked, self.template, self._author, target.platform)
        method = "api" if page is not None else "clipboard"
        cache_id = None
        if self._cache is not None:
            # 보관 스냅샷에는 본인 이름 머리글을 넣지 않는다(개인정보 최소 저장).
            snapshot = to_platform_text(
                masked, self.template, self._author, target.platform, include_owner=False
            )
            link = page.link if page is not None else target.link
            cache_id = self._cache.add(
                approved.draft.week_start,
                target.platform.value,
                method,
                link,
                self._clock.now(),
                snapshot,
            )
        return Delivery(
            method,
            target.platform,
            text,
            approved.preview.section_html,
            approved.preview.title,
            page,
            reason,
            cache_id,
        )
