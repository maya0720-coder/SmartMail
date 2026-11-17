"""주간보고 HTML 편집·복사 형식 API(C1·C2, WIKI-02·05, 결정기록 0041).

- `POST /api/weekly/clean-html`: HTML 편집 탭 입력을 서버 허용 목록으로 정리·마스킹한 결과와
  버린 태그·속성 요약(저장하지 않음, 실시간 미리보기용)
- `GET  /api/weekly/formats`: 복사 형식 7종(이름·게시 방식)
입력 HTML 원문은 저장·로그하지 않는다.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from domain.errors import UserInputError
from domain.weekly.rich import MAX_HTML_BYTES, sanitize_report, unmasked_text_left
from domain.weekly.service import SPLIT_SENSITIVE
from domain.weekly.targets import FORMATS

router = APIRouter(prefix="/api/weekly")


class CleanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    html: str = Field(max_length=MAX_HTML_BYTES)


@router.post("/clean-html")
def clean_html_preview(request: Request, body: CleanBody) -> JSONResponse:
    bases = get_container(request).weekly.link_bases
    try:
        clean, removed = sanitize_report(body.html, bases)
    except UserInputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "html": clean,
            "removed": removed,
            # 서식 사이에 걸친 민감정보가 남으면 미리보기·게시를 거부한다(0030 규칙)
            "blocked": SPLIT_SENSITIVE if unmasked_text_left(clean) else None,
        }
    )


@router.get("/formats")
def list_formats() -> JSONResponse:
    return JSONResponse(
        [
            {
                "key": key.value,
                "label": info.label,
                "platform": info.platform.value,
                "direct": info.direct,
                "action": info.action,
                "has_html": info.has_html,
            }
            for key, info in FORMATS.items()
        ]
    )
