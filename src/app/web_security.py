"""웹 보안 미들웨어: 보안 헤더, CSRF, Origin·Host 검사.

- 로컬 서버라도 다른 사이트가 브라우저를 통해 127.0.0.1로 요청을 보낼 수 있으므로(CSRF·DNS 리바인딩)
  Host 허용 목록, 상태 변경 요청의 CSRF 토큰·Origin·JSON 형식을 모두 검사한다.
- CSP는 인라인 스크립트·스타일을 허용하지 않는다.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

CSRF_HEADER: Final = "x-csrf-token"
UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ALLOWED_HOSTS: Final = ("127.0.0.1", "localhost")
# 접속한 클라이언트 IP가 이 목록에 없으면 거부한다(0.0.0.0 바인딩 실수 대비).
LOOPBACK_CLIENTS: Final = ("127.0.0.1", "::1")

CSP: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)

SECURITY_HEADERS: Final = {
    "content-security-policy": CSP,
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}

# 요청 본문 최대 크기(바이트). 주간보고·지식 등록 본문을 고려한 상한.
MAX_BODY_BYTES: Final = 256 * 1024

CSRF_REJECTED_MESSAGE: Final = "요청을 확인할 수 없습니다. 화면을 새로고침한 뒤 다시 시도해 주세요."


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _forbidden() -> JSONResponse:
    return JSONResponse({"error": CSRF_REJECTED_MESSAGE}, status_code=403)


def _too_large(request: Request) -> bool:
    length = request.headers.get("content-length")
    if length is None:
        return request.method in UNSAFE_METHODS and "chunked" in request.headers.get(
            "transfer-encoding", ""
        )
    return not length.isdigit() or int(length) > MAX_BODY_BYTES


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        csrf_token: str,
        port: int | None,
        trusted_clients: tuple[str, ...] = LOOPBACK_CLIENTS,
    ) -> None:
        super().__init__(app)
        self._clients = frozenset(trusted_clients)
        self._token = csrf_token
        self._origins = (
            None if port is None else frozenset(f"http://{host}:{port}" for host in ALLOWED_HOSTS)
        )

    def _csrf_ok(self, request: Request) -> bool:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not secrets.compare_digest(sent, self._token):
            return False
        origin = request.headers.get("origin")
        if origin is not None and self._origins is not None and origin not in self._origins:
            return False
        content_type = request.headers.get("content-type", "")
        return content_type.split(";")[0].strip().lower() == "application/json"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        client = request.client.host if request.client else ""
        if client not in self._clients:
            response: Response = _forbidden()
        elif request.method in UNSAFE_METHODS and not self._csrf_ok(request):
            response = _forbidden()
        elif _too_large(request):
            response = JSONResponse({"error": "요청이 너무 큽니다."}, status_code=413)
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if request.url.path.startswith("/api/") or request.method in UNSAFE_METHODS:
            response.headers["cache-control"] = "no-store"
        if "server" in response.headers:
            del response.headers["server"]
        return response
