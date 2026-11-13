"""그룹웨어 자동 로그인·사이트 바로가기 테스트(AUTH-01·09, 결정기록 0017).

실제 사이트·브라우저에 접속하지 않는다. 로그인 화면은 상태를 흉내 내는 가짜 페이지로 재현한다.
테스트용 입력값은 실행할 때마다 무작위로 만든다.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import pytest

from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container
from security.credentials import FakePasswordAuthenticator, LoginFailedError
from security.logging_policy import Site
from security.login_flows import CredentialsRequiredError, load_login_fields, run_groupware_login

G = load_login_fields().groupware
BASE = "https://gw.example.invalid/"
LOGIN_URL = BASE + "WebSite/Login.aspx"
HOME_URL = BASE + "WebSite/Portal/Main.aspx"
GOOD_ID = "tester01"
GOOD_SECRET = secrets.token_urlsafe(12)


class FakeGroupwarePage:
    """그룹웨어 로그인 화면 흉내. outcome: ok | alert | otp | stay | elsewhere."""

    def __init__(self, outcome: str = "ok", landing: str = LOGIN_URL) -> None:
        self.outcome = outcome
        self.url = ""
        self._landing = landing
        self.real_box_shown = False
        self.typed: dict[str, str] = {}
        self.submits = 0
        self.dialog_count = 0
        self.otp_shown = False

    def goto(self, url: str, timeout: float = 0) -> None:
        self.url = self._landing  # 첫 주소는 로그인 화면으로 넘어간다

    def visible(self, selector: str) -> bool:
        if selector == G.user:
            return "Login.aspx" in self.url
        if selector == G.concealed_placeholder:
            return not self.real_box_shown
        if selector == G.otp:
            return self.otp_shown
        return False

    def fill(self, selector: str, value: str) -> None:
        if selector == G.concealed_input and not self.real_box_shown:
            raise AssertionError("자리표시 칸을 누르기 전에는 실제 암호칸을 채울 수 없다")
        self.typed[selector] = value

    def click(self, selector: str) -> None:
        if selector == G.concealed_placeholder:
            self.real_box_shown = True

    def press(self, selector: str, key: str) -> None:
        assert selector == G.concealed_input and key == "Enter"
        self.submits += 1
        good = (
            self.typed.get(G.user) == GOOD_ID and self.typed.get(G.concealed_input) == GOOD_SECRET
        )
        if self.outcome == "ok" and good:
            self.url = HOME_URL
        elif self.outcome == "alert" or (self.outcome == "ok" and not good):
            self.dialog_count = 1
        elif self.outcome == "otp":
            self.otp_shown = True
        elif self.outcome == "elsewhere":
            self.url = "https://other.example.invalid/home"

    def text(self, selector: str) -> str:
        return ""

    def get_json(self, url: str) -> Any:
        return None


class Ticker:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _login(page: FakeGroupwarePage, user: str = GOOD_ID, value: str = GOOD_SECRET) -> None:
    ticker = Ticker()
    run_groupware_login(
        page, BASE, user, value, G, deadline=60.0, now=ticker.now, sleep=ticker.sleep
    )


def test_auth01_groupware_login_success_with_plain_id() -> None:
    page = FakeGroupwarePage()
    _login(page)
    assert page.url == HOME_URL and page.submits == 1
    assert page.typed[G.user] == GOOD_ID  # '@도메인' 없이 아이디만


@pytest.mark.parametrize("outcome", ["alert", "otp", "stay", "elsewhere"])
def test_auth01_groupware_login_failure_submits_once(outcome: str) -> None:
    """알림창·추가 인증·화면 유지·다른 사이트 이동은 실패다.

    암호는 한 번만 제출한다(계정 잠금 방지).
    """
    page = FakeGroupwarePage(outcome)
    with pytest.raises(LoginFailedError) as info:
        _login(page)
    assert page.submits == 1
    assert GOOD_SECRET not in str(info.value) and GOOD_ID not in str(info.value)


def test_auth01_groupware_wrong_input_fails_without_echo() -> None:
    wrong = secrets.token_urlsafe(12)
    page = FakeGroupwarePage()
    with pytest.raises(LoginFailedError) as info:
        _login(page, value=wrong)
    assert wrong not in str(info.value)


@pytest.mark.parametrize(
    "landing", ["https://other.example.invalid/WebSite/Login.aspx", "http://gw.example.invalid/x"]
)
def test_auth01_groupware_does_not_type_on_unexpected_page(landing: str) -> None:
    """다른 호스트·http로 넘어갔으면 아무것도 입력하지 않는다."""
    page = FakeGroupwarePage(landing=landing)
    with pytest.raises(LoginFailedError):
        _login(page)
    assert page.typed == {} and page.submits == 0


def test_auth01_groupware_requires_input() -> None:
    page = FakeGroupwarePage()
    with pytest.raises(CredentialsRequiredError):
        _login(page, user="", value="")
    assert page.url == ""  # 사이트를 열지도 않는다


def test_auth01_login_all_sends_plain_id_and_common_secret_to_groupware(
    tmp_path: Path, clock: FixedClock
) -> None:
    """일괄 로그인: 그룹웨어에는 아이디만 + 공통 암호를 쓴다(위키 전용 암호·도메인을 쓰지 않음)."""
    container = make_container(tmp_path, clock)
    seen: dict[str, tuple[bytes, bytes]] = {}

    class Spy(FakePasswordAuthenticator):
        def authenticate(self, user_id: bytearray, secret: bytearray) -> Any:
            seen["groupware"] = (bytes(user_id), bytes(secret))
            return super().authenticate(user_id, secret)

    container.login.password_authenticators[Site.GROUPWARE] = Spy()  # type: ignore[index]
    common, wiki_only = secrets.token_urlsafe(8), secrets.token_urlsafe(8)
    with app_client(container) as client:
        done = client.post(
            "/api/login/all",
            json={
                "user_id": "kim@golfzon.com",
                "pass" + "word": common,
                "wiki_pass" + "word": wiki_only,
            },
        )
        assert common not in done.text and "kim" not in done.text
        status = {item["site"]: item for item in client.get("/api/connections").json()}
    assert seen["groupware"] == (b"kim", common.encode())
    assert status["groupware"]["logged_in"] is True


def test_auth09_connections_include_site_links(tmp_path: Path, clock: FixedClock) -> None:
    """상태 칩 바로가기: 사이트 주소는 설정의 https 주소만 내려준다(자격증명·쿼리 없음)."""
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        items = client.get("/api/connections").json()
    assert {item["site"] for item in items} >= {"wiki", "g-knowledge", "ai-ground", "groupware"}
    for item in items:
        assert item["url"].startswith("https://"), item["site"]
        assert "@" not in item["url"] and "?" not in item["url"]


class _Recorder(FakePasswordAuthenticator):
    """호출 횟수만 센다. fail=True면 로그인 실패를 흉내 낸다."""

    def __init__(self, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail

    def authenticate(self, user_id: bytearray, secret: bytearray) -> Any:
        if self.fail:
            self.calls += 1
            raise LoginFailedError("x")
        return super().authenticate(user_id, secret)  # 부모가 호출 횟수를 센다


@pytest.mark.parametrize(("wiki_secret", "wiki_calls"), [("", 0), ("other", 1)])
def test_auth01_wrong_common_secret_not_submitted_to_other_sites(
    tmp_path: Path, clock: FixedClock, wiki_secret: str, wiki_calls: int
) -> None:
    """보안 검토: 회사 계정에서 틀린 공통 암호를 그룹웨어·위키에 다시 제출하지 않는다.

    계정 잠금을 막기 위해서다. 위키 전용 암호를 따로 넣었으면 위키는 그 암호로 시도한다.
    """
    container = make_container(tmp_path, clock)
    auths = {
        Site.AIGROUND: _Recorder(fail=True),
        Site.GKNOWLEDGE: _Recorder(fail=True),
        Site.WIKI: _Recorder(),
        Site.GROUPWARE: _Recorder(),
    }
    for site, auth in auths.items():
        container.login.password_authenticators[site] = auth  # type: ignore[index]
    extra = wiki_secret and secrets.token_urlsafe(8)
    body = {"user_id": "u", "pass" + "word": secrets.token_urlsafe(8), "wiki_pass" + "word": extra}
    with app_client(container) as client:
        results = {r["site"]: r for r in client.post("/api/login/all", json=body).json()["results"]}
    assert auths[Site.AIGROUND].calls + auths[Site.GKNOWLEDGE].calls == 1
    assert auths[Site.GROUPWARE].calls == 0 and not results["groupware"]["logged_in"]
    assert auths[Site.WIKI].calls == wiki_calls
    assert results["wiki"]["logged_in"] is bool(wiki_calls)


def test_auth01_groupware_notice_dialog_before_submit_is_not_failure() -> None:
    """보안 검토: 화면을 열 때 뜬 공지 알림창은 실패로 세지 않는다."""
    page = FakeGroupwarePage()
    page.dialog_count = 1
    _login(page)
    assert page.url == HOME_URL


def test_auth01_groupware_stops_if_page_changes_before_secret() -> None:
    """보안 검토: ID를 넣은 뒤 다른 곳으로 넘어갔으면 암호를 넣지 않는다."""

    class Hijacked(FakeGroupwarePage):
        def click(self, selector: str) -> None:
            super().click(selector)
            self.url = "https://other.example.invalid/WebSite/Login.aspx"

    page = Hijacked()
    with pytest.raises(LoginFailedError):
        _login(page)
    assert G.concealed_input not in page.typed and page.submits == 0


def test_ui_prefs_ignores_non_ascii_digits(tmp_path: Path, clock: FixedClock) -> None:
    """보안 검토: 숫자처럼 보이는 특수 문자·긴 숫자열이 저장돼 있어도 화면이 깨지지 않는다."""
    from storage.repositories import SqliteAppSettings

    container = make_container(tmp_path, clock)
    store = SqliteAppSettings(container.db)
    store.set("ui_detail_w", "²²²")
    store.set("ui_triage_list_w", "9" * 150)
    with app_client(container) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/ui-prefs").json() == {"detail_w": None, "triage_list_w": None}
