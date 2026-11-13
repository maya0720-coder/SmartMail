"""업무 구분 목록 조회·변경 API(TODO-01 개정, 결정기록 0014)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from domain.errors import UserInputError
from domain.tasks.categories import MAX_CATEGORIES, MAX_CATEGORY_CHARS

router = APIRouter(prefix="/api/task-categories")

Name = Annotated[str, Field(min_length=1, max_length=MAX_CATEGORY_CHARS)]


class CategoriesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[Name] = Field(min_length=1, max_length=MAX_CATEGORIES)


@router.get("")
def get_categories(request: Request) -> JSONResponse:
    container = get_container(request)
    return JSONResponse(
        {
            "categories": list(container.tasks.categories()),
            "approval_category": container.catalog.rules.approval_category,
            "max": MAX_CATEGORIES,
        }
    )


@router.put("")
def put_categories(request: Request, body: CategoriesBody) -> JSONResponse:
    container = get_container(request)
    try:
        saved = container.catalog.save(body.categories)
    except UserInputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    # 목록에서 빠진 구분을 쓰던 업무는 규칙으로 다시 구분한다
    changed = container.tasks.recategorize_unknown()
    return JSONResponse({"categories": list(saved), "recategorized": changed})
