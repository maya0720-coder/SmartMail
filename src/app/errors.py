"""오류 응답 정책.

사용자에게는 일반 메시지만 돌려주고 내부 경로·스택·쿼리·입력값은 숨긴다.
디버그 모드(로컬 개발 전용, 기본 꺼짐)에서도 예외 종류 이름만 추가로 보여준다.
상세 내용은 서버 로그(민감정보 필터 적용)에만 남긴다.

주의: FastAPI/Starlette의 `debug=True`는 등록한 처리기보다 앞서 스택 추적을 응답하므로
프레임워크 debug는 항상 꺼 두고, 설정의 debug는 이 모듈의 `debug` 인자로만 넘긴다.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE: Final = "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
VALIDATION_ERROR_MESSAGE: Final = "입력값을 확인해 주세요."

# 오류 응답에 그대로 전달해도 되는 표준 헤더(소문자). 그 외 헤더는 버린다.
ALLOWED_ERROR_HEADERS: Final = frozenset({"allow", "www-authenticate", "retry-after"})

_STATUS_MESSAGES: Final[dict[int, str]] = {
    HTTPStatus.BAD_REQUEST: "잘못된 요청입니다.",
    HTTPStatus.UNAUTHORIZED: "로그인이 필요합니다.",
    HTTPStatus.FORBIDDEN: "접근 권한이 없습니다.",
    HTTPStatus.NOT_FOUND: "요청한 항목을 찾을 수 없습니다.",
    HTTPStatus.METHOD_NOT_ALLOWED: "허용되지 않은 요청 방식입니다.",
    HTTPStatus.CONFLICT: "다른 변경과 충돌했습니다. 새로고침 후 다시 시도해 주세요.",
    HTTPStatus.TOO_MANY_REQUESTS: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
}


def _body(message: str, exc: Exception, debug: bool) -> dict[str, str]:
    body = {"error": message}
    if debug:
        body["debug_type"] = type(exc).__name__
    return body


def _allowed_headers(exc: Exception) -> dict[str, str] | None:
    headers = getattr(exc, "headers", None) or {}
    kept = {k: v for k, v in headers.items() if k.lower() in ALLOWED_ERROR_HEADERS}
    return kept or None


def register_error_handlers(app: FastAPI, *, debug: bool = False) -> None:
    """FastAPI 앱에 공통 오류 처리기를 등록한다.

    Raises:
        RuntimeError: 앱의 프레임워크 debug가 켜져 있을 때(스택 추적 노출 방지)
    """
    if app.debug:
        raise RuntimeError(
            "FastAPI debug는 켤 수 없습니다. 설정 debug는 register_error_handlers로 넘기세요"
        )

    async def handle_http(request: Request, exc: Exception) -> JSONResponse:
        status = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
        message = _STATUS_MESSAGES.get(status, GENERIC_ERROR_MESSAGE)
        return JSONResponse(
            _body(message, exc, debug), status_code=status, headers=_allowed_headers(exc)
        )

    async def handle_validation(request: Request, exc: Exception) -> JSONResponse:
        # FastAPI 기본 응답은 입력값을 그대로 돌려주므로(암호 등 노출 위험) 일반 메시지로 바꾼다.
        return JSONResponse(
            _body(VALIDATION_ERROR_MESSAGE, exc, debug),
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        )

    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error("처리되지 않은 예외: path=%s", request.url.path, exc_info=exc)
        return JSONResponse(
            _body(GENERIC_ERROR_MESSAGE, exc, debug),
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    app.add_exception_handler(StarletteHTTPException, handle_http)
    app.add_exception_handler(RequestValidationError, handle_validation)
    app.add_exception_handler(Exception, handle_unexpected)
