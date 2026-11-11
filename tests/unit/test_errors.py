"""오류 응답 정책 테스트(개발지침 2장 오류·로그)."""

from __future__ import annotations

import secrets

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.errors import (
    GENERIC_ERROR_MESSAGE,
    VALIDATION_ERROR_MESSAGE,
    register_error_handlers,
)


class LoginForm(BaseModel):
    site: str
    attempts: int


def _client(debug: bool = False) -> TestClient:
    app = FastAPI(debug=False)
    register_error_handlers(app, debug=debug)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError(r"C:\internal\path query SELECT * FROM t")

    @app.get("/headers")
    def headers() -> None:
        raise HTTPException(
            status_code=401,
            headers={"WWW-Authenticate": "Basic", "X-Debug-Info": "C:/internal/path"},
        )

    @app.get("/forbidden")
    def forbidden() -> None:
        raise HTTPException(status_code=403, detail="user hong cannot read task 42")

    @app.post("/login")
    def login(form: LoginForm) -> dict[str, str]:
        return {"site": form.site}

    return TestClient(app, raise_server_exceptions=False)


def test_unexpected_error_hides_internal_details() -> None:
    response = _client().get("/boom")
    assert response.status_code == 500
    assert response.json() == {"error": GENERIC_ERROR_MESSAGE}
    assert "internal" not in response.text
    assert "Traceback" not in response.text


def test_debug_mode_adds_only_exception_type() -> None:
    response = _client(debug=True).get("/boom")
    assert response.json() == {"error": GENERIC_ERROR_MESSAGE, "debug_type": "RuntimeError"}


def test_http_error_detail_replaced_with_generic_message() -> None:
    response = _client().get("/forbidden")
    assert response.status_code == 403
    assert "hong" not in response.text


def test_unknown_route_returns_generic_404() -> None:
    response = _client().get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]


def test_auth03_validation_error_does_not_echo_input() -> None:
    generated = secrets.token_hex(8)  # 실행마다 새로 만든 가짜 입력값
    bad_number = "not-a-number-" + secrets.token_hex(4)
    payload = {"site": "wiki", "attempts": bad_number, "extra_field": generated}
    response = _client().post("/login", json=payload)
    assert response.status_code == 422
    assert response.json() == {"error": VALIDATION_ERROR_MESSAGE}
    assert generated not in response.text
    assert bad_number not in response.text


def test_framework_debug_mode_refused() -> None:
    """FastAPI debug=True는 처리기보다 먼저 스택 추적을 응답하므로 거부한다(보안 검토 M-4)."""
    with pytest.raises(RuntimeError):
        register_error_handlers(FastAPI(debug=True))


def test_error_headers_limited_to_allowlist() -> None:
    response = _client().get("/headers")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"
    assert "x-debug-info" not in response.headers
