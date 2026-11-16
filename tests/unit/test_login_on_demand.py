"""필요할 때 일괄 로그인 서버 쪽 테스트(AUTH-01·05·09, 결정기록 0034).

- /api/connections가 이번 실행에서 로그인이 필요한 사이트(needs_login)를 알려 준다.
- /api/login/all이 사이트 목록(sites)만 로그인한다. 합성 ID·임의 암호만 쓴다.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Any

import pytest

from app.routes.login_all import RATE_LIMITED
from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container
from security.credentials import FakePasswordAuthenticator, LoginFailedError
from security.logging_policy import Site


def _body() -> dict[str, str]:
    return {"user_id": "tester01", "pass" + "word": secrets.token_urlsafe(12)}


def test_auth01_connections_report_needs_login(tmp_path: Path, clock: FixedClock) -> None:
    """데모(가짜 어댑터)는 로그인 없이 동작하므로 needs_login이 모두 false다."""
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        items = {i["site"]: i for i in client.get("/api/connections").json()}
        assert set(items) == {"wiki", "g-knowledge", "ai-ground", "groupware"}
        assert not any(i["needs_login"] for i in items.values())
        container.login_free_sites = frozenset({Site.GROUPWARE})  # 실제 어댑터를 쓰는 실행
        items = {i["site"]: i for i in client.get("/api/connections").json()}
        assert items["ai-ground"]["needs_login"] and items["wiki"]["needs_login"]
        assert not items["groupware"]["needs_login"]


def test_auth01_login_all_only_requested_sites(tmp_path: Path, clock: FixedClock) -> None:
    """sites를 주면 그 사이트만 로그인한다(이번 작업에 필요한 사이트만, 결정기록 0034)."""
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        body = _body()
        done = client.post("/api/login/all", json={**body, "sites": ["ai-ground"]})
        assert done.status_code == 200
        assert body["pass" + "word"] not in done.text and "tester01" not in done.text
        assert [(r["site"], r["logged_in"]) for r in done.json()["results"]] == [
            ("ai-ground", True)
        ]
        pending = client.get("/api/login/pending").json()
        assert pending["sites"] == ["g-knowledge", "wiki"]
        # 회사 계정이 연결돼 있으면 입력 없이 SSO로 다른 회사 계정 사이트를 연결한다
        assert pending["need_company"] is False
        # 직접 로그인 사이트(그룹웨어)도 목록에 넣을 수 있다
        both = client.post("/api/login/all", json={**_body(), "sites": ["wiki", "groupware"]})
        assert {(r["site"], r["logged_in"]) for r in both.json()["results"]} == {
            ("wiki", True),
            ("groupware", True),
        }
        # 이미 로그인된 사이트만 지정하면 아무것도 하지 않는다
        assert client.post("/api/login/all", json={"sites": ["wiki"]}).json()["results"] == []


def test_auth01_login_all_sites_validation(tmp_path: Path, clock: FixedClock) -> None:
    """사이트 목록은 알려진 이름만, 비지 않게, only와 함께 쓰지 않는다."""
    with app_client(make_container(tmp_path, clock)) as client:
        assert client.post("/api/login/all", json={"sites": []}).status_code == 422
        assert client.post("/api/login/all", json={"sites": ["evil"]}).status_code == 422
        assert client.post("/api/login/all", json={"sites": ["wiki"] * 5}).status_code == 422
        both = client.post("/api/login/all", json={"sites": ["wiki"], "only": "wiki"})
        assert both.status_code == 400
        assert client.get("/api/login/pending").json()["sites"] == [
            "ai-ground",
            "g-knowledge",
            "wiki",
        ]


class _CountingService:
    """요약 서비스 대역: 호출 횟수만 센다."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, *_args: object) -> object:  # pragma: no cover - 호출되면 실패
        self.calls += 1
        raise AssertionError("로그인 전에는 호출하지 않아야 합니다")


def test_auth09_background_summary_skips_quietly_without_login(clock: FixedClock) -> None:
    """백그라운드 요약은 로그인 전 AI 호출 0회이고 화면 안내 문구만 남긴다(창을 띄우지 않음)."""
    from datetime import timedelta

    from app.summary_job import LOGIN_REQUIRED, SummaryJob
    from security.sessions import SessionStore

    service = _CountingService()
    job = SummaryJob(service, SessionStore(clock, timedelta(minutes=30)), always_ready=False)  # type: ignore[arg-type]
    assert not job.ready()
    job.run_once()
    assert service.calls == 0 and job.state.message == LOGIN_REQUIRED


def test_auth01_sites_login_requires_csrf(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        client.headers.pop("X-CSRF-Token")
        response = client.post("/api/login/all", json={**_body(), "sites": ["wiki"]})
        assert response.status_code == 403


class _Failing(FakePasswordAuthenticator):
    """틀린 암호로 실패하는 가짜 인증기(호출 횟수만 센다)."""

    def authenticate(self, user_id: bytearray, secret: bytearray) -> Any:
        self.calls += 1
        raise LoginFailedError("x")


def test_auth01_sites_wrong_common_password_not_resubmitted(
    tmp_path: Path, clock: FixedClock
) -> None:
    """보안 검토 L1: 위키가 공통 암호로 실패하면 같은 요청의 그룹웨어에 다시 제출하지 않는다."""
    container = make_container(tmp_path, clock)
    wiki, groupware = _Failing(), _Failing()
    container.login.password_authenticators[Site.WIKI] = wiki  # type: ignore[index]
    container.login.password_authenticators[Site.GROUPWARE] = groupware  # type: ignore[index]
    with app_client(container) as client:
        results = client.post(
            "/api/login/all", json={**_body(), "sites": ["wiki", "groupware"]}
        ).json()["results"]
    assert wiki.calls == 1 and groupware.calls == 0
    assert all(not r["logged_in"] for r in results)


def test_auth01_sites_company_failure_blocks_common_sites(
    tmp_path: Path, clock: FixedClock
) -> None:
    """회사 계정이 틀리면 sites 경로에서도 위키·그룹웨어에 같은 암호를 제출하지 않는다(M4)."""
    container = make_container(tmp_path, clock)
    ai, gk, wiki, gw = _Failing(), _Failing(), _Failing(), _Failing()
    for site, auth in (
        (Site.AIGROUND, ai),
        (Site.GKNOWLEDGE, gk),
        (Site.WIKI, wiki),
        (Site.GROUPWARE, gw),
    ):
        container.login.password_authenticators[site] = auth  # type: ignore[index]
    sites = ["ai-ground", "g-knowledge", "wiki", "groupware"]
    with app_client(container) as client:
        client.post("/api/login/all", json={**_body(), "sites": sites})
    assert ai.calls + gk.calls + wiki.calls + gw.calls == 1


def test_auth01_company_rate_limit_blocks_common_sites(tmp_path: Path, clock: FixedClock) -> None:
    """보안 검토 L3: 회사 계정이 한도 초과(확인 안 됨)면 공통 암호를 다른 사이트에 내지 않는다."""
    container = make_container(tmp_path, clock)
    for _ in range(10):  # 회사 계정 시도 한도를 다 쓴다
        container.login_limiter.check("company-account")
    wiki = _Failing()
    container.login.password_authenticators[Site.WIKI] = wiki  # type: ignore[index]
    with app_client(container) as client:
        results = client.post(
            "/api/login/all", json={**_body(), "sites": ["ai-ground", "wiki"]}
        ).json()["results"]
    assert wiki.calls == 0
    assert {r["site"]: r["error"] for r in results}["wiki"] == RATE_LIMITED


def test_auth05_sso_attempts_do_not_use_password_limit(tmp_path: Path, clock: FixedClock) -> None:
    """보안 검토 L2: 입력 없는 SSO 시도를 여러 번 해도 실제 암호 로그인 한도는 남아 있다."""
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        for _ in range(10):
            client.post("/api/login/all", json={"sites": ["ai-ground"]})
        done = client.post("/api/login/all", json={**_body(), "sites": ["ai-ground"]}).json()
    assert [(r["site"], r["logged_in"]) for r in done["results"]] == [("ai-ground", True)]


def test_auth01_sites_rejects_query_and_logs_no_secret(
    tmp_path: Path, clock: FixedClock, caplog: pytest.LogCaptureFixture
) -> None:
    """주소에 값이 붙은 요청은 거부하고, 로그에 ID·암호가 남지 않는다."""
    body = _body()
    with caplog.at_level(logging.DEBUG), app_client(make_container(tmp_path, clock)) as client:
        assert (
            client.post("/api/login/all?x=1", json={**body, "sites": ["wiki"]}).status_code == 400
        )
        client.post("/api/login/all", json={**body, "sites": ["wiki"]})
    assert body["pass" + "word"] not in caplog.text and "tester01" not in caplog.text
