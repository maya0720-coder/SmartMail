"""주간보고 양식 프로필 API(N4, WIKI-02, 결정기록 0037).

- `GET    /api/weekly/links/{id}/profile`: 저장된 양식 구조·칸 연결(없으면 null)
- `POST   /api/weekly/links/{id}/profile/import`: 위키 로그인 세션으로 읽어 등록
  (위키 링크만)
- `POST   /api/weekly/links/{id}/profile/paste`: 붙여넣은 HTML(내 영역)로 등록
- `PUT    /api/weekly/links/{id}/profile/bindings`: 칸 연결 저장
- `POST   /api/weekly/links/{id}/profile/check`: 위키 세션이 있을 때만 양식 바뀜 확인
  (로그인 창 없음)
- `DELETE /api/weekly/links/{id}/profile`
양식 HTML은 저장·로그하지 않는다. 구조(머리글·열 이름·고정 문구, 마스킹)만 저장한다.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from domain.errors import UserInputError
from domain.weekly.service import WikiLoginRequiredError, WikiUnavailableError
from domain.weekly.template_profile import MAX_BLOCKS, MAX_COLUMNS, MAX_SOURCE_BYTES
from security.logging_policy import EventType, Site, log_event

router = APIRouter(prefix="/api/weekly/links")

SOURCE_LABELS = {
    "done": "금주 실적",
    "planned": "차주 계획",
    "issues": "이슈·요청",
    "fixed": "고정 문구",
    "none": "비움",
}
FIELD_LABELS = {
    "title": "업무 제목",
    "module": "구분",
    "dept": "요청 부서",
    "status": "상태",
    "due": "기한",
    "closed": "완료일",
    "progress": "진행 요약",
    "note": "비고(빈칸)",
}
SourceName = Literal["done", "planned", "issues", "fixed", "none"]
FieldName = Literal["title", "module", "dept", "status", "due", "closed", "progress", "note"]
BlockKey = Annotated[str, Field(pattern=r"^\d{1,3}$")]


class PasteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    html: str = Field(min_length=1, max_length=MAX_SOURCE_BYTES)


class BindingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headings: dict[BlockKey, SourceName] = Field(default_factory=dict, max_length=MAX_BLOCKS)
    columns: dict[BlockKey, Annotated[list[FieldName], Field(max_length=MAX_COLUMNS)]] = Field(
        default_factory=dict, max_length=MAX_BLOCKS
    )


def _error(message: str, status: int, code: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": message}
    if code:
        body["code"] = code
    return JSONResponse(body, status_code=status)


def _profile_response(request: Request, link_id: int, **extra: Any) -> JSONResponse:
    profile = get_container(request).weekly_profiles.get(link_id)
    return JSONResponse(
        {
            "profile": None if profile is None else profile.as_json(),
            "sources": SOURCE_LABELS,
            "fields": FIELD_LABELS,
            **extra,
        }
    )


def _known_link(request: Request, link_id: int) -> JSONResponse | None:
    if get_container(request).weekly_links.get(link_id) is None:
        return _error("링크를 찾을 수 없습니다", 404)
    return None


@router.get("/{link_id}/profile")
def get_profile(request: Request, link_id: int) -> JSONResponse:
    return _known_link(request, link_id) or _profile_response(request, link_id)


@router.post("/{link_id}/profile/import")
def import_profile(request: Request, link_id: int) -> JSONResponse:
    if (missing := _known_link(request, link_id)) is not None:
        return missing
    try:
        get_container(request).weekly_profiles.import_from_wiki(link_id)
    except WikiLoginRequiredError as exc:
        return _error(str(exc), 503, "login_required")
    except WikiUnavailableError as exc:
        log_event(EventType.WIKI_READ, Site.WIKI, success=False)
        return _error(str(exc), 503)
    except UserInputError as exc:
        return _error(str(exc), 400)
    log_event(EventType.WIKI_READ, Site.WIKI, success=True)
    return _profile_response(request, link_id)


@router.post("/{link_id}/profile/paste")
def paste_profile(request: Request, link_id: int, body: PasteBody) -> JSONResponse:
    if (missing := _known_link(request, link_id)) is not None:
        return missing
    try:
        get_container(request).weekly_profiles.import_from_paste(link_id, body.html)
    except UserInputError as exc:
        return _error(str(exc), 400)
    return _profile_response(request, link_id)


@router.put("/{link_id}/profile/bindings")
def save_bindings(request: Request, link_id: int, body: BindingsBody) -> JSONResponse:
    if (missing := _known_link(request, link_id)) is not None:
        return missing
    try:
        get_container(request).weekly_profiles.save_bindings(link_id, body.model_dump())
    except UserInputError as exc:
        return _error(str(exc), 400)
    return _profile_response(request, link_id)


@router.post("/{link_id}/profile/check")
def check_profile(request: Request, link_id: int) -> JSONResponse:
    if (missing := _known_link(request, link_id)) is not None:
        return missing
    _profile, changed, checked = get_container(request).weekly_profiles.check(link_id)
    return _profile_response(request, link_id, changed=changed, checked=checked)


@router.delete("/{link_id}/profile")
def delete_profile(request: Request, link_id: int) -> JSONResponse:
    if not get_container(request).weekly_profiles.delete(link_id):
        return _error("등록된 양식이 없습니다", 404)
    return JSONResponse({"deleted": link_id})
