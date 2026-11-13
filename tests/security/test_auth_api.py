"""로그인 API·웹 보안 테스트(AUTH-01~05, AUTH-10, CSRF)."""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from app.bootstrap import build_container
from domain.clock import FixedClock
from fixtures.app_factory import (
    BASE_URL,
    FakeBackend,
    app_client,
    make_container,
    make_settings,
)
from security.browser import BrowserRuntime
from security.logging_policy import configure_logging, uninstall_record_sanitizer
from storage.db import Database


def _creds() -> dict[str, str]:
    return {"user_id": "tester-" + secrets.token_hex(4), "password": secrets.token_hex(16)}


def test_auth10_credentials_never_reach_disk_or_logs(tmp_path: Path, clock: FixedClock) -> None:
    """로그인·로그아웃·종료 후 DB·로그·임시 폴더 어디에도 테스트 ID·암호가 없어야 한다."""
    # 데모 모드는 메모리 DB를 쓰므로, 디스크 검사를 위해 파일 DB를 직접 넘긴다.
    settings = make_settings(tmp_path)
    container = build_container(
        settings,
        clock=clock,
        db=Database(settings.db_path),
        browser=BrowserRuntime(backend_factory=FakeBackend),
    )
    configure_logging(tmp_path / "logs" / "app.log")
    creds = _creds()
    try:
        with app_client(container) as client:
            response = client.post("/api/login", json={"site": "wiki", **creds})
            assert response.status_code == 200
            assert creds["password"] not in response.text
            status = {i["site"]: i["logged_in"] for i in client.get("/api/connections").json()}
            assert status["wiki"] is True
            client.post("/api/logout-all")
    finally:
        uninstall_record_sanitizer()
        import logging

        for handler in list(logging.getLogger().handlers):
            if getattr(handler, "baseFilename", "").startswith(str(tmp_path)):
                logging.getLogger().removeHandler(handler)
                handler.close()
    container.db.close()
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert any(p.suffix == ".db" for p in files)
    assert any(p.suffix == ".log" for p in files)
    for path in files:
        data = path.read_bytes()
        assert creds["user_id"].encode() not in data, path.name
        assert creds["password"].encode() not in data, path.name


def test_login_rejects_query_string(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        response = client.post("/api/login?user_id=a", json={"site": "wiki", **_creds()})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"site": "wiki", "user_id": "", "password": "x"},
        {"site": "wiki", "user_id": "a", "password": ""},
        {"site": "wiki", "user_id": "a" * 101, "password": "x"},
        {"site": "wiki", "user_id": "a", "password": "x" * 257},
        {"site": "evil", "user_id": "a", "password": "x"},
        {"site": "wiki", "user_id": "a", "password": "x", "remember": True},
    ],
)
def test_invalid_login_input_rejected_without_echo(
    tmp_path: Path, clock: FixedClock, body: dict[str, object]
) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        response = client.post("/api/login", json=body)
    assert response.status_code in (400, 422)
    assert "a" * 50 not in response.text
    assert "x" * 50 not in response.text


def test_login_failure_generic_message(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock, demo_mode=False)
    creds = _creds()
    with app_client(container) as client:
        # 위키는 자동 로그인 어댑터가 있다(결정기록 0011). 가짜 브라우저라 로그인은 실패한다.
        response = client.post("/api/login", json={"site": "wiki", **creds})
        assert response.status_code == 401
        assert creds["user_id"] not in response.text
        # 그룹웨어는 규격 미확인
        response = client.post("/api/login", json={"site": "groupware", **creds})
        assert response.status_code == 503
        assert creds["user_id"] not in response.text


def test_csrf_token_required(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        client.headers.pop("X-CSRF-Token")
        response = client.post("/api/login", json={"site": "wiki", **_creds()})
        assert response.status_code == 403
        wrong = client.post(
            "/api/logout-all", json={}, headers={"X-CSRF-Token": secrets.token_urlsafe(32)}
        )
        assert wrong.status_code == 403


def test_cross_origin_and_form_posts_rejected(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        evil = client.post("/api/logout-all", json={}, headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403
        form = client.post("/api/logout-all", data={"a": "b"})
        assert form.status_code == 403
        same = client.post("/api/logout-all", json={}, headers={"Origin": BASE_URL})
        assert same.status_code == 200


def test_untrusted_host_rejected(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        response = client.get("/settings", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_rate_limit_on_login(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        codes = [
            client.post("/api/login", json={"site": "wiki", **_creds()}).status_code
            for _ in range(6)
        ]
    assert codes[:5] == [200] * 5
    assert codes[5] == 429


def test_security_headers_present(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        response = client.get("/settings")
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "server" not in response.headers


def test_auth04_login_form_disables_autocomplete(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        html = client.get("/settings").text
    assert '<form id="st-form" autocomplete="off">' in html  # 시작 로그인 창
    assert 'type="password" name="x-pw" autocomplete="new-password"' in html
    assert "저장하지 않으며" in html
    assert "remember" not in html.lower() and "기억하기" not in html


def test_auth05_logout_closes_session(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        client.post("/api/login", json={"site": "wiki", **_creds()})
        client.post("/api/logout", json={"site": "wiki"})
        status = {item["site"]: item["logged_in"] for item in client.get("/api/connections").json()}
    assert status["wiki"] is False


def test_data_wipe_requires_confirm(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        assert client.post("/api/data/wipe", json={}).status_code == 422
        assert client.post("/api/data/wipe", json={"confirm": True}).status_code == 200


def test_api_docs_disabled(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
