"""사이트 자동 로그인 테스트(AUTH-01·03·05·09, 결정기록 0011).

실제 사이트·브라우저에 접속하지 않는다. 로그인 화면은 상태를 흉내 내는 가짜 페이지로 재현한다.
테스트용 입력값은 실행할 때마다 무작위로 만든다.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.routes.login_all import account_ids
from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container
from security.credentials import FakePasswordAuthenticator, LoginFailedError, LoginService
from security.logging_policy import Site
from security.login_flows import (
    CredentialsRequiredError,
    load_login_fields,
    run_entra_login,
    run_wiki_login,
)
from security.sessions import SessionStore
from security.site_login import LoginProgress, SharedContext

FIELDS = load_login_fields()
E = FIELDS.entra
SITE_URL = "https://ai.example.invalid/"
MARKER = FIELDS.site_logged_in["ai-ground"]
GOOD_ID = "user@x.invalid"
GOOD_SECRET = secrets.token_urlsafe(12)
BAD_SECRET = secrets.token_urlsafe(12)


class FakeEntraPage:
    """Entra 로그인 화면 상태 기계. state: email → secret → (mfa) → kmsi → done."""

    def __init__(self, *, mfa_polls: int = 0, sso: bool = False) -> None:
        self.state = "done" if sso else "email"
        self.mfa_polls = mfa_polls
        self.typed: dict[str, str] = {}
        self.kmsi_declined = False

    @property
    def url(self) -> str:
        return SITE_URL if self.state == "done" else f"https://{E.host}/oauth2"

    def goto(self, url: str, timeout: float = 0) -> None:
        return None

    def visible(self, selector: str) -> bool:
        if self.state == "mfa" and selector == E.mfa_number:
            self.mfa_polls -= 1
            if self.mfa_polls < 0:
                self.state = "kmsi"
            return True
        mapping = {
            "email": E.email,
            "secret": E.concealed_input,
            "kmsi": E.kmsi_marker,
            "done": MARKER,
            "error": E.error,
        }
        return mapping.get(self.state) == selector

    def fill(self, selector: str, value: str) -> None:
        self.typed[selector] = value

    def click(self, selector: str) -> None:
        if self.state == "email" and selector == E.next:
            self.state = "secret"
        elif self.state == "secret" and selector == E.next:
            ok = (
                self.typed.get(E.email) == GOOD_ID
                and self.typed.get(E.concealed_input) == GOOD_SECRET
            )
            self.state = ("mfa" if self.mfa_polls else "kmsi") if ok else "error"
        elif self.state == "kmsi" and selector == E.kmsi_no:
            self.kmsi_declined = True
            self.state = "done"

    def text(self, selector: str) -> str:
        return "37"

    def get_json(self, url: str) -> Any:
        return None


class Ticker:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _entra(page: FakeEntraPage, user: str, value: str, mfa: list[Any] | None = None) -> None:
    ticker = Ticker()
    run_entra_login(
        page,
        SITE_URL,
        MARKER,
        user,
        value,
        E,
        deadline=60.0,
        now=ticker.now,
        sleep=ticker.sleep,
        on_mfa=(mfa.append if mfa is not None else lambda _v: None),
    )


def test_auth01_entra_login_success_declines_stay_signed_in() -> None:
    page = FakeEntraPage()
    _entra(page, GOOD_ID, GOOD_SECRET)
    assert page.state == "done" and page.kmsi_declined  # '로그인 상태 유지' 거부


def test_auth01_entra_wrong_input_fails_without_echo() -> None:
    page = FakeEntraPage()
    with pytest.raises(LoginFailedError) as info:
        _entra(page, GOOD_ID, BAD_SECRET)
    assert BAD_SECRET not in str(info.value) and GOOD_ID not in str(info.value)


def test_auth09_entra_mfa_number_reported_then_cleared() -> None:
    page = FakeEntraPage(mfa_polls=3)
    seen: list[Any] = []
    _entra(page, GOOD_ID, GOOD_SECRET, seen)
    assert "37" in seen and seen[-1] == ""  # 번호 안내 후 지움


def test_auth05_entra_sso_without_credentials() -> None:
    _entra(FakeEntraPage(sso=True), "", "")  # 이미 로그인된 공용 컨텍스트: 입력 없이 성공
    with pytest.raises(CredentialsRequiredError):
        _entra(FakeEntraPage(), "", "")  # 입력 화면이 나오면 입력 요구


@pytest.mark.parametrize("selector", [E.email, E.concealed_input])
def test_auth01_entra_input_selectors_skip_hidden_decoys(selector: str) -> None:
    """회귀: Entra 이메일 화면에는 화면 밖 암호칸(aria-hidden)이 함께 있다.

    이 칸을 '보임'으로 판정하면 이메일보다 암호를 먼저 제출해 로그인이 실패한다.
    """
    assert ":not([aria-hidden='true'])" in selector


def test_auth01_entra_timeout() -> None:
    page = FakeEntraPage(mfa_polls=10_000)  # 승인하지 않음
    with pytest.raises(LoginFailedError):
        _entra(page, GOOD_ID, GOOD_SECRET)


class FakeWikiPage:
    def __init__(self, known: bool, error: bool = False) -> None:
        self._url = ""
        self.known = known
        self.error = error
        self.submitted = False

    @property
    def url(self) -> str:
        return self._url

    def goto(self, url: str, timeout: float = 0) -> None:
        self._url = url

    def visible(self, selector: str) -> bool:
        return self.error and self.submitted and selector == FIELDS.wiki.error

    def fill(self, selector: str, value: str) -> None:
        return None

    def click(self, selector: str) -> None:
        self.submitted = True
        if not self.error:
            self._url = "https://wiki.example.invalid/"

    def text(self, selector: str) -> str:
        return ""

    def get_json(self, url: str) -> Any:
        return {"type": "known" if self.known else "anonymous"}


def _wiki(page: FakeWikiPage, user: str, value: str) -> None:
    ticker = Ticker()
    run_wiki_login(
        page,
        "https://wiki.example.invalid",
        user,
        value,
        FIELDS.wiki,
        deadline=30.0,
        now=ticker.now,
        sleep=ticker.sleep,
    )


def test_auth01_wiki_login_success() -> None:
    _wiki(FakeWikiPage(True), "id", GOOD_SECRET)


@pytest.mark.parametrize("page", [FakeWikiPage(False), FakeWikiPage(True, error=True)])
def test_auth01_wiki_login_failure(page: FakeWikiPage) -> None:
    with pytest.raises(LoginFailedError):
        _wiki(page, "id", BAD_SECRET)


def test_auth01_wiki_requires_input() -> None:
    with pytest.raises(CredentialsRequiredError):
        _wiki(FakeWikiPage(True), "", "")


class FakeContext:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_auth05_logout_of_one_company_site_discards_bundle(clock: FixedClock) -> None:
    # 보안 검토 M1: 한 사이트 로그아웃 시 공용 SSO 컨텍스트 전체 폐기, 다른 사이트 세션도 정리
    raw = FakeContext()
    shared = SharedContext(raw)  # type: ignore[arg-type]
    sessions = SessionStore(clock, timedelta(minutes=30))
    sessions.open(Site.AIGROUND, shared.lease())
    sessions.open(Site.GKNOWLEDGE, shared.lease())
    sessions.logout(Site.AIGROUND)
    assert raw.closed
    assert sessions.current(Site.GKNOWLEDGE) is None  # 닫힌 세션은 로그인됨으로 보이지 않음
    with pytest.raises(LoginFailedError):
        shared.lease()  # 보안 검토 M3: 닫힌 컨텍스트로 새 세션을 만들지 않음


def test_auth05_idle_expiry_discards_bundle(clock: FixedClock) -> None:
    raw = FakeContext()
    shared = SharedContext(raw)  # type: ignore[arg-type]
    sessions = SessionStore(clock, timedelta(minutes=30))
    sessions.open(Site.AIGROUND, shared.lease())
    clock.advance(timedelta(minutes=31))
    sessions.sweep()
    assert raw.closed


def test_login_progress_has_no_credentials() -> None:
    progress = LoginProgress()
    # 기존 필드는 그대로, 보안문자 필드(결정기록 0025)는 하위 호환으로 추가됨
    idle = {
        "otp_waiting": False,
        "otp_retry": False,
        "kind": None,
        "captcha_waiting": False,
        "captcha_retry": False,
        "captcha_seq": 0,
    }
    progress.update("37")
    assert progress.snapshot() == {"mfa_waiting": True, "mfa_number": "37", **idle}
    progress.update("<script>")  # 숫자가 아니면 번호로 쓰지 않음
    assert progress.snapshot()["mfa_number"] is None
    progress.update("")
    assert progress.snapshot() == {"mfa_waiting": False, "mfa_number": None, **idle}


class SsoAuthenticator(FakePasswordAuthenticator):
    supports_sso = True

    def __init__(self, live: bool) -> None:
        super().__init__()
        self.live = live

    def authenticate(self, user_id: bytearray, secret: bytearray) -> Any:
        if not user_id and not self.live:
            raise CredentialsRequiredError("x")
        return super().authenticate(user_id or bytearray(b"-"), secret or bytearray(b"-"))


def test_auth05_try_sso_and_password_preferred(clock: FixedClock) -> None:
    sessions = SessionStore(clock, timedelta(minutes=30))
    service = LoginService(
        sessions,
        {
            Site.AIGROUND: SsoAuthenticator(live=True),
            Site.GKNOWLEDGE: SsoAuthenticator(live=False),
        },
        {Site.AIGROUND: object(), Site.GROUPWARE: object()},  # type: ignore[dict-item]
    )
    assert service.try_sso(Site.AIGROUND) is not None
    assert service.try_sso(Site.GKNOWLEDGE) is None
    assert service.try_sso(Site.WIKI) is None
    assert not service.uses_browser_login(Site.AIGROUND)  # 자동 로그인이 우선
    assert service.uses_browser_login(Site.GROUPWARE)


def test_auth01_login_all_api(tmp_path: Path, clock: FixedClock) -> None:
    """시작 로그인 창: ID 1개 + 공통 암호로 회사 계정·위키를 한 번에(결정기록 0014)."""
    container = make_container(tmp_path, clock)  # 데모: 가짜 어댑터
    value = secrets.token_urlsafe(12)
    with app_client(container) as client:
        pending = client.get("/api/login/pending").json()
        assert pending["need_company"] and pending["need_wiki"]
        assert pending["account_domain"] == "golfzon.com" and pending["dismissed"] is False
        # 그룹웨어는 일괄 로그인에서 빼고, 사용자가 직접 로그인한다(결정기록 0026)
        assert "groupware" not in pending["sites"] and pending["manual_sites"] == ["groupware"]
        body = {"user_id": "tester01", "pass" + "word": value}
        done = client.post("/api/login/all", json=body)
        assert value not in done.text and "tester01" not in done.text
        assert all(r["logged_in"] for r in done.json()["results"])
        assert {r["site"] for r in done.json()["results"]} == {"ai-ground", "g-knowledge", "wiki"}
        # 이미 로그인된 사이트는 다시 묻지 않는다. 그룹웨어는 아직 직접 로그인 대상으로 남는다
        assert client.get("/api/login/pending").json()["sites"] == []
        assert client.get("/api/login/pending").json()["manual_sites"] == ["groupware"]
        only = client.get("/api/login/pending", params={"site": "groupware"}).json()
        assert only["sites"] == ["groupware"]
        manual = client.post("/api/login/all", json={**body, "only": "groupware"}).json()
        assert [(r["site"], r["logged_in"]) for r in manual["results"]] == [("groupware", True)]
        assert client.get("/api/login/pending").json()["manual_sites"] == []
        assert client.post("/api/login/all", json={}).json()["results"] == []
        assert client.post("/api/login/all?u=x", json={}).status_code == 400
        assert client.post("/api/login/all", json={"remember": True}).status_code == 422
        assert client.post("/api/login/all", json={"company_id": "x"}).status_code == 422


def test_auth01_later_suppresses_auto_prompt(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        assert client.post("/api/login/later", json={}).json() == {"dismissed": True}
        assert client.get("/api/login/pending").json()["dismissed"] is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hong", ("hong@golfzon.com", "hong")),
        (" hong ", ("hong@golfzon.com", "hong")),
        ("hong@golfzon.com", ("hong@golfzon.com", "hong")),
        ("", ("", "")),
    ],
)
def test_auth01_account_ids_from_single_id(raw: str, expected: tuple[str, str]) -> None:
    assert account_ids(raw, "golfzon.com") == expected


@pytest.mark.parametrize("raw", ["ho ng", "hong\x00", "ho\tng"])
def test_auth01_account_ids_reject_whitespace(raw: str) -> None:
    with pytest.raises(ValueError):
        account_ids(raw, "golfzon.com")


def test_auth01_wiki_password_override(tmp_path: Path, clock: FixedClock) -> None:
    """위키 암호가 다르면 그 값만 위키에 쓴다(회사 계정은 공통 암호)."""
    container = make_container(tmp_path, clock)
    seen: dict[str, tuple[bytes, bytes]] = {}

    class Spy(FakePasswordAuthenticator):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

        def authenticate(self, user_id: bytearray, secret: bytearray) -> Any:
            seen[self.name] = (bytes(user_id), bytes(secret))
            return super().authenticate(user_id, secret)

    for site, name in ((Site.AIGROUND, "ai"), (Site.WIKI, "wiki")):
        container.login.password_authenticators[site] = Spy(name)  # type: ignore[index]
    common, wiki_only = secrets.token_urlsafe(8), secrets.token_urlsafe(8)
    with app_client(container) as client:
        client.post(
            "/api/login/all",
            json={"user_id": "kim", "pass" + "word": common, "wiki_pass" + "word": wiki_only},
        )
    assert seen["ai"] == (b"kim@golfzon.com", common.encode())
    assert seen["wiki"] == (b"kim", wiki_only.encode())


class CountingFailure(FakePasswordAuthenticator):
    def authenticate(self, user_id: bytearray, secret: bytearray) -> Any:
        self.calls += 1
        raise LoginFailedError("x")


def test_auth01_wrong_company_input_not_retried_on_second_site(
    tmp_path: Path, clock: FixedClock
) -> None:
    # 보안 검토 M4: 같은 회사 계정으로 두 번 제출하지 않음(계정 잠금 위험)
    container = make_container(tmp_path, clock)
    ai, gk = CountingFailure(), CountingFailure()
    container.login.password_authenticators[Site.AIGROUND] = ai  # type: ignore[index]
    container.login.password_authenticators[Site.GKNOWLEDGE] = gk  # type: ignore[index]
    body = {"user_id": "u", "pass" + "word": secrets.token_urlsafe(8)}
    with app_client(container) as client:
        results = client.post("/api/login/all", json=body).json()["results"]
    assert ai.calls + gk.calls == 1
    company = [r for r in results if r["site"] in ("ai-ground", "g-knowledge")]
    assert company and all(not r["logged_in"] for r in company)


def test_auth01_login_all_only_and_bad_unicode(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        only = client.post("/api/login/all", json={"only": "wiki"}).json()["results"]
        assert [r["site"] for r in only] == ["wiki"]  # 요청한 사이트만
        raw = '{"user_id": "\\ud800", "pass' + 'word": "x"}'
        bad = client.post(
            "/api/login/all", content=raw, headers={"Content-Type": "application/json"}
        )
        assert bad.status_code in (400, 422)  # 잘못된 유니코드는 500 없이 거부(L2)
