"""외부 회사 도메인 관리 API(결정기록 0027): 조회·확정(수정)·삭제.

회사 도메인과 회사명만 다룬다(개인정보 아님). 사용자가 확정한 값은 `source='user'`로 저장되어
같은 도메인의 모든 업무에 적용되고, 매핑 파일·AI 추정값보다 우선한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_container
from domain.tasks.requester import MAX_COMPANY_CHARS, MAX_DOMAIN_CHARS, normalize_domain

router = APIRouter(prefix="/api/companies")

Domain = Annotated[str, Field(min_length=3, max_length=MAX_DOMAIN_CHARS)]
INVALID = "도메인 또는 회사명 형식이 올바르지 않습니다(회사명 50자 이하, 주소·숫자만 불가)."


class CompanyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Domain
    company: str = Field(min_length=1, max_length=MAX_COMPANY_CHARS)


@router.get("")
def list_companies(request: Request) -> JSONResponse:
    container = get_container(request)
    return JSONResponse(
        [
            {"domain": domain, "company": name.company, "source": name.source}
            for domain, name in container.company_cache.list_all()
        ]
    )


@router.put("")
def confirm_company(request: Request, body: CompanyBody) -> JSONResponse:
    container = get_container(request)
    try:
        saved = container.companies.confirm(body.domain, body.company)
    except ValueError:
        return JSONResponse({"error": INVALID}, status_code=400)
    return JSONResponse(
        {"domain": normalize_domain(body.domain), "company": saved.company, "source": saved.source}
    )


@router.delete("/{domain}")
def delete_company(request: Request, domain: str) -> JSONResponse:
    normalized = normalize_domain(domain)
    if normalized is None:
        return JSONResponse({"error": INVALID}, status_code=400)
    deleted = get_container(request).company_cache.delete(normalized)
    return JSONResponse({"deleted": deleted}, status_code=200 if deleted else 404)
