"""주간보고 API(WIKI-01~06): 초안 → 편집 → 미리보기 → 승인 → 게시."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from domain.calendar import week_start
from domain.clock import KST
from domain.errors import UserInputError
from domain.weekly.form import MAX_HEADING_CHARS, FormLayout, layout_from_client
from domain.weekly.links import Platform
from domain.weekly.report import (
    MAX_ITEM_CHARS,
    MAX_ITEMS,
    OwnSectionError,
    WeeklyDraft,
    classify_week_tasks,
    item_text,
    render_body,
    with_layout,
)
from domain.weekly.rich import MAX_HTML_BYTES, sanitize
from domain.weekly.service import (
    DeliveryTarget,
    WikiConflictError,
    WikiUnavailableError,
)
from security.links import safe_link
from security.logging_policy import EventType, Site, log_event

router = APIRouter(prefix="/api/weekly")

# 편집기 HTML 글자 수 상한(바이트 상한 200KB는 서버 정리기가 다시 검사한다)
MAX_HTML_FIELD_CHARS = MAX_HTML_BYTES

Item = Annotated[str, Field(min_length=1, max_length=MAX_ITEM_CHARS)]


Heading = Annotated[str, Field(min_length=1, max_length=MAX_HEADING_CHARS)]


class Headings(BaseModel):
    """참고 링크 양식의 머리글(초안 응답이 돌려준 값을 화면이 그대로 되돌려 보낸다)."""

    model_config = ConfigDict(extra="forbid")

    done: Heading
    planned: Heading
    issues: Heading


class FormIn(BaseModel):
    """초안 응답의 `form`을 그대로 되돌려 보낸 값. 머리글만 쓰고 나머지는 안내용이다."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(default="", max_length=20)
    label: str = Field(default="", max_length=300)
    headings: Headings


class DraftRequest(BaseModel):
    """초안 요청. 참고 링크를 고르면 그 양식의 머리글 구조로 만든다(없으면 기본 양식)."""

    model_config = ConfigDict(extra="forbid")

    link_id: int | None = Field(default=None, ge=1)


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form: FormIn | None = None
    week_start: datetime
    done: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    planned: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    issues: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    # 미리보기 편집기 HTML(결정기록 0030). 있으면 목록 대신 쓴다. 서버가 정리·마스킹한다
    html: str | None = Field(default=None, max_length=MAX_HTML_FIELD_CHARS)
    # 초안 응답의 draft_html을 그대로 되돌려 보낸 값(하위 호환). 쓰지 않는다 — 편집 내용은 html로
    draft_html: str | None = Field(default=None, max_length=MAX_HTML_FIELD_CHARS)
    page_title: str | None = Field(default=None, max_length=200)  # 되돌려 보낸 값(쓰지 않음)
    own_heading: str | None = Field(default=None, max_length=200)  # 되돌려 보낸 값(쓰지 않음)
    # 초안 응답의 양식 구조(결정기록 0037)를 되돌려 보낸 값. 쓰지 않는다(서버 저장값이 기준)
    profile: dict[str, Any] | None = None

    def to_draft(self) -> WeeklyDraft:
        if self.week_start.tzinfo is None or week_start(self.week_start) != self.week_start:
            raise UserInputError("주 시작 시각이 올바르지 않습니다")
        layout = None
        if self.form is not None:
            heads = self.form.headings
            layout = layout_from_client(heads.done, heads.planned, heads.issues)
        return WeeklyDraft(
            self.week_start,
            list(self.done),
            list(self.planned),
            list(self.issues),
            layout,
            self.html,
        )


class PublishBody(DraftBody):
    nonce: str = Field(min_length=16, max_length=64)  # 미리보기 때 서버가 발급한 1회용 번호
    mode: Literal["create", "update"]
    approved: Literal[True]  # 사용자가 '승인 후 게시'를 눌렀을 때만 True
    target_link_id: int | None = Field(default=None, ge=1)  # 게시 대상 참고 링크(없으면 위키)
    fallback: bool = False  # API 게시가 안 될 때 클립보드 폴백 허용(화면에서 켠다)


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _form_json(layout: FormLayout) -> dict[str, object]:
    done, planned, issues = layout.headings()
    return {
        "source": layout.source,
        "label": layout.label,
        "headings": {"done": done, "planned": planned, "issues": issues},
    }


def _draft_json(
    draft: WeeklyDraft, layout: FormLayout, draft_html: str | None = None
) -> dict[str, object]:
    return {
        # 편집기 초기값(결정기록 0030): 템플릿·양식으로 만든 정리된 HTML(본인 머리글 제외)
        "draft_html": draft_html,
        "week_start": draft.week_start.astimezone(KST).isoformat(),
        "done": draft.done,
        "planned": draft.planned,
        "issues": draft.issues,
        "form": _form_json(layout),  # 사용한 양식 안내(필드 추가만, 기존 필드는 그대로)
    }


@router.post("/draft")
def make_draft(
    request: Request, body: Annotated[DraftRequest | None, Body()] = None
) -> JSONResponse:
    container = get_container(request)
    try:
        link = container.weekly_links.get(body.link_id) if body and body.link_id else None
        if body and body.link_id and link is None:
            raise UserInputError("선택한 참고 링크를 찾을 수 없습니다")
    except UserInputError as exc:
        return _error(str(exc), 400)
    layout = container.weekly.resolve_layout(link)
    draft = container.weekly.draft()
    profile = container.weekly_profiles.get(link.id) if link is not None else None
    if profile is not None:
        # 조직 양식 프로필 순서대로 초안을 만든다(결정기록 0037)
        template = container.weekly.template
        sections = classify_week_tasks(container.tasks.list_tasks(), container.clock.now())
        section = container.weekly_profiles.render(
            profile, sections, lambda task: item_text(task, template)
        )
        layout = replace(layout, label=f"양식 프로필 {profile.name}", source="link")
    else:
        section = render_body(draft, with_layout(container.weekly.template, layout))
    result = _draft_json(draft, layout, sanitize(section))
    # 양식 구조(편집기 잠금·비교·되돌리기용). 없으면 '기본 양식 사용 중' 안내를 띄운다
    result["profile"] = None if profile is None else profile.as_json()
    # 편집기 위쪽에 읽기 전용(회색)으로 보일 페이지 제목·본인 영역 머리글(WIKI-04)
    result["page_title"] = container.weekly.template.page_title(draft.week_start)
    result["own_heading"] = container.weekly.own_heading
    return JSONResponse(result)


@router.post("/preview")
def preview(request: Request, body: DraftBody) -> JSONResponse:
    try:
        result = get_container(request).weekly.preview(body.to_draft())
    except UserInputError as exc:
        return _error(str(exc), 400)
    return JSONResponse(
        {
            "title": result.title,
            "section_html": result.section_html,
            "nonce": result.nonce,
            "base_version": result.base_version,
            "current_section": result.current_section,
        }
    )


@router.post("/publish")
def publish(request: Request, body: PublishBody) -> JSONResponse:
    container = get_container(request)
    weekly = container.weekly
    wiki_base = str(container.settings.wiki_base_url)
    try:
        target = DeliveryTarget()
        if body.target_link_id is not None:
            link = container.weekly_links.get(body.target_link_id)
            if link is None:
                raise UserInputError("선택한 참고 링크를 찾을 수 없습니다")
            target = DeliveryTarget(link.platform, link.url)
        approved = weekly.approve(body.to_draft(), body.nonce, body.mode)
        delivery = weekly.deliver(approved, target, allow_fallback=body.fallback)
    except (WikiConflictError, OwnSectionError) as exc:
        log_event(EventType.WIKI_POST, Site.WIKI, success=False)
        return _error(str(exc), 409)
    except WikiUnavailableError as exc:
        log_event(EventType.WIKI_POST, Site.WIKI, success=False)
        return _error(str(exc), 503)
    except UserInputError as exc:
        return _error(str(exc), 400)
    site = Site.WIKI if delivery.platform is Platform.WIKI else Site.APP
    log_event(EventType.WIKI_POST, site, success=True)
    page = delivery.page
    open_target = target.link or wiki_base
    return JSONResponse(
        {
            "title": delivery.title,
            "version": None if page is None else page.version,
            "link": None if page is None else safe_link(page.link, [wiki_base]),
            "method": delivery.method,
            "platform": delivery.platform.value,
            "text": delivery.text,
            "html": delivery.html,
            "reason": delivery.reason,
            # 폴백이면 화면이 이 https 주소를 새 창으로 연다(noopener). 검증을 통과한 주소만 준다.
            "open_link": None
            if delivery.method == "api"
            else safe_link(open_target, [open_target]),
            "cache_id": delivery.cache_id,
        }
    )


@router.get("/history")
def history(request: Request) -> JSONResponse:
    container = get_container(request)
    records = container.publish_log.list_recent()
    wiki_base = [str(container.settings.wiki_base_url)]
    return JSONResponse(
        [
            {
                "week_start": r.week_start.astimezone(KST).isoformat(),
                "published_at": r.published_at.astimezone(KST).isoformat(),
                "version": r.page_version,
                "link": safe_link(r.page_link or "", wiki_base),
            }
            for r in records
        ]
    )
