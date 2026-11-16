"""주간보고 참고 링크 관리·형식 변환·게시 캐시 API(WIKI-01·05·06)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from app.routes.weekly import DraftBody
from domain.clock import KST
from domain.errors import UserInputError
from domain.weekly.form import MAX_OUTLINE_LINES
from domain.weekly.links import (
    MAX_LINKS,
    MAX_NAME_CHARS,
    MAX_URL_CHARS,
    PLATFORM_LABELS,
    Platform,
    ReferenceLink,
)
from security.links import safe_link

router = APIRouter(prefix="/api/weekly")

OUTLINE_INPUT_CHARS = 3000  # 붙여 넣는 양식 목차 전체 글자 수 상한


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=MAX_URL_CHARS)
    name: str = Field(default="", max_length=MAX_NAME_CHARS)
    outline: str = Field(default="", max_length=OUTLINE_INPUT_CHARS)


class ConvertBody(DraftBody):
    platform: Literal["slack", "wiki", "web"]


def _link_json(link: ReferenceLink) -> dict[str, object]:
    return {
        "id": link.id,
        "url": link.url,
        "href": safe_link(link.url, [link.url]),  # 화면 링크는 검증을 통과한 https만
        "name": link.name,
        "platform": link.platform.value,
        "platform_label": PLATFORM_LABELS[link.platform],
        "outline_lines": len(link.outline.splitlines()) if link.outline else 0,
    }


@router.get("/links")
def list_links(request: Request) -> JSONResponse:
    links = get_container(request).weekly_links.list_all()
    return JSONResponse(
        {
            "links": [_link_json(link) for link in links],
            "max_links": MAX_LINKS,
            "max_outline_lines": MAX_OUTLINE_LINES,
        }
    )


@router.post("/links")
def add_link(request: Request, body: LinkBody) -> JSONResponse:
    try:
        link = get_container(request).weekly_links.add(body.url, body.name, body.outline)
    except UserInputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(_link_json(link), status_code=201)


@router.delete("/links/{link_id}")
def delete_link(request: Request, link_id: int) -> JSONResponse:
    if not get_container(request).weekly_links.delete(link_id):
        return JSONResponse({"error": "링크를 찾을 수 없습니다"}, status_code=404)
    return JSONResponse({"deleted": link_id})


@router.post("/convert")
def convert(request: Request, body: ConvertBody) -> JSONResponse:
    """초안을 마스킹한 뒤 대상 플랫폼 형식으로 바꾼다. 게시하지 않으므로 승인 번호가 필요 없다."""
    try:
        text, html = get_container(request).weekly.convert(body.to_draft(), Platform(body.platform))
    except UserInputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"platform": body.platform, "text": text, "html": html})


@router.get("/cache")
def list_cache(request: Request) -> JSONResponse:
    entries = get_container(request).weekly_cache.list_recent()
    return JSONResponse(
        [
            {
                "id": e.id,
                "week_start": e.week_start.astimezone(KST).isoformat(),
                "published_at": e.published_at.astimezone(KST).isoformat(),
                "platform": e.platform,
                "platform_label": PLATFORM_LABELS[Platform(e.platform)],
                "method": e.method,
                "link": None if e.link is None else safe_link(e.link, [e.link]),
            }
            for e in entries
        ]
    )


@router.get("/cache/{entry_id}")
def get_cache(request: Request, entry_id: int) -> JSONResponse:
    entry = get_container(request).weekly_cache.get(entry_id)
    if entry is None:
        return JSONResponse({"error": "게시 캐시를 찾을 수 없습니다"}, status_code=404)
    return JSONResponse({"id": entry.id, "platform": entry.platform, "text": entry.snapshot})
