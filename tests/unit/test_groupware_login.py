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
from security.credentials import FakePasswordAuthenticator, LoginFailedError, OtpFailedError
from security.groupware_captcha import ErrorCategory
from security.logging_policy import Site
from security.login_flows import (
    MAX_OTP_TRIES,
    CredentialsRequiredError,
    load_login_fields,
    run_groupware_login,
)

G = load_login_fields().groupware
BASE = "https://gw.example.invalid/"
LOGIN_URL = BASE + "WebSite/Login.aspx"
HOME_URL = BASE + "WebSite/Portal/Main.aspx"
GOOD_ID = "tester01"
GOOD_SECRET = secrets.token_urlsafe(12)


class FakeGroupwarePage:
    """그룹웨어 로그인 화면 흉내. outcome: ok | alert | otp | stay | elsewhere.

    otp: 암호 제출 뒤 인증문자 입력칸이 나온다. good_otp와 같은 번호를 넣으면 로그인된다.
    """

    def __init__(self, outcome: str = "ok", landing: str = LOGIN_URL, good_otp: str = "") -> None:
        self.outcome = outcome
        self.url = ""
        self._landing = landing
        self.real_box_shown = False
        self.typed: dict[str, str] = {}
        self.submits = 0
        self.otp_submits = 0
        self.dialog_count = 0
        self.otp_notice_count = 0
        self.otp_shown = False
        self.good_otp = good_otp
        # 보안문자 지원 메서드(이 화면에는 보안문자가 없다: visible이 항상 False)
        self.captcha_notice_count = 0
        self.navigation_count = 0
        self.last_error_category = ErrorCategory.OTHER

    def goto(self, url: str, timeout: float = 0) -> None:
        self.url = self._landing  # 첫 주소는 로그인 화면으로 넘어간다

    def visible(self, selector: str) -> bool:
        if selector == G.user:
            return "Login.aspx" in self.url
        if selector == G.concealed_placeholder:
            return not self.real_box_shown
        if selector == G.otp_input:
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
        assert key == "Enter"
        if selector == G.otp_input:
            assert self.otp_shown, "인증문자 칸이 없을 때 번호를 제출하면 안 된다"
            self.otp_submits += 1
            if self.good_otp and self.typed.get(G.otp_input) == self.good_otp:
                self.url, self.otp_shown = HOME_URL, False
            else:
                self.dialog_count += 1  # 번호가 틀리면 알림창
            return
        assert selector == G.concealed_input
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
        elif self.outcome == "otp_notice_page":
            # 인증번호 발송 알림창이 먼저 뜨고, 주소에 login이 없는 인증 화면으로 넘어간다
            self.otp_notice_count += 1
            self.otp_shown = True
            self.url = BASE + "WebSite/Auth/SmsCheck.aspx"
        elif self.outcome == "otp_with_alert":
            # 인증 칸이 처음부터 보이는 화면에서 암호 오류 알림창이 뜬다
            self.otp_shown = True
            self.dialog_count = 1
        elif self.outcome == "notice_only":
            self.otp_notice_count += 1  # 안내만 뜨고 입력칸은 찾지 못함
        elif self.outcome == "elsewhere":
            self.url = "https://other.example.invalid/home"

    def text(self, selector: str) -> str:
        return ""

    def capture(self, selector: str) -> bytes | None:
        return None

    def attribute(self, selector: str, name: str) -> str:
        return ""

    def has_value(self, selector: str) -> bool:
        return bool(self.typed.get(selector))

    def hidden_names(self) -> list[str]:
        return []

    def error_category(self, selector: str) -> ErrorCategory | None:
        return None

    def get_json(self, url: str) -> Any:
        return None


class Ticker:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _login(
    page: FakeGroupwarePage,
    user: str = GOOD_ID,
    value: str = GOOD_SECRET,
    ask_otp: Any = None,
) -> None:
    ticker = Ticker()
    run_groupware_login(
        page,
        BASE,
        user,
        value,
        G,
        deadline=180.0,
        now=ticker.now,
        sleep=ticker.sleep,
        ask_otp=ask_otp or (lambda _retry, _timeout: None),  # 기본: 번호 입력 취소
    )


class OtpAnswers:
    """화면에서 사용자가 넣는 인증문자 번호를 차례로 돌려준다. 요청(재입력 여부)을 기록한다."""

    def __init__(self, *codes: str | None) -> None:
        self._codes = list(codes)
        self.asks: list[bool] = []

    def __call__(self, retry: bool, timeout: float) -> str | None:
        assert timeout > 0
        self.asks.append(retry)
        return self._codes.pop(0) if self._codes else None


def _otp() -> str:
    return str(secrets.randbelow(900_000) + 100_000)


def test_auth01_groupware_otp_entered_from_app_screen() -> None:
    """인증문자 화면이 나오면 앱 화면에서 받은 번호를 넣어 로그인한다(결정기록 0020)."""
    code = _otp()
    page = FakeGroupwarePage("otp", good_otp=code)
    answers = OtpAnswers(code)
    _login(page, ask_otp=answers)
    assert page.url == HOME_URL
    assert answers.asks == [False] and page.otp_submits == 1 and page.submits == 1


def test_auth01_groupware_otp_after_notice_on_non_login_page() -> None:
    """인증번호 발송 알림창이 먼저 떠도 실패로 보지 않는다.

    인증 화면 주소에 login이 없어도 성공으로 오인하지 않는다.
    """
    code = _otp()
    page = FakeGroupwarePage("otp_notice_page", good_otp=code)
    answers = OtpAnswers(code)
    _login(page, ask_otp=answers)
    assert page.url == HOME_URL and answers.asks == [False] and page.otp_submits == 1


def test_auth01_groupware_notice_without_input_is_otp_failure() -> None:
    """인증번호 안내는 떴는데 입력칸을 찾지 못하면 '인증문자 실패'로 구분해 안내한다."""
    page = FakeGroupwarePage("notice_only")
    with pytest.raises(OtpFailedError):
        _login(page)
    assert page.submits == 1


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("인증번호가 발송되었습니다.", True),
        ("SMS 인증을 진행합니다", True),
        ("아이디 또는 비밀번호가 틀렸습니다.", False),
        ("공지사항", False),
    ],
)
def test_auth01_groupware_dialog_classification(message: str, expected: bool) -> None:
    from security.site_login import is_otp_notice

    assert is_otp_notice(message) is expected


def test_auth01_groupware_otp_retry_after_wrong_code() -> None:
    """번호가 틀리면 다시 묻는다(재입력 안내). 암호는 다시 제출하지 않는다."""
    code = _otp()
    page = FakeGroupwarePage("otp", good_otp=code)
    answers = OtpAnswers("000000", code)
    _login(page, ask_otp=answers)
    assert page.url == HOME_URL
    assert answers.asks == [False, True] and page.otp_submits == 2 and page.submits == 1


def test_auth01_groupware_otp_gives_up_after_max_tries() -> None:
    """번호를 계속 틀리면 정해진 횟수만 제출하고 인증문자 실패로 끝낸다."""
    page = FakeGroupwarePage("otp", good_otp=_otp())
    with pytest.raises(OtpFailedError) as info:
        _login(page, ask_otp=OtpAnswers(*["000000"] * 10))
    assert page.otp_submits == MAX_OTP_TRIES and page.submits == 1
    assert "000000" not in str(info.value)


def test_auth01_groupware_otp_cancel_stops_without_submit() -> None:
    """번호 입력을 취소하거나 시간이 지나면 아무것도 제출하지 않고 인증문자 실패로 끝낸다."""
    page = FakeGroupwarePage("otp", good_otp=_otp())
    with pytest.raises(OtpFailedError):
        _login(page, ask_otp=OtpAnswers(None))
    assert page.otp_submits == 0 and G.otp_input not in page.typed


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


def test_auth01_groupware_waits_for_login_page_to_appear() -> None:
    """문서만 준비된 상태에서 시작하므로, 로그인 화면으로 넘어갈 때까지 잠시 기다린 뒤 입력한다."""

    page = FakeGroupwarePage(landing=BASE)
    ticker = Ticker()

    def sleep(seconds: float) -> None:
        ticker.sleep(seconds)
        if ticker.t >= 1.0 and page.url == BASE:
            page.url = LOGIN_URL  # 1초 뒤에 로그인 화면으로 넘어감

    run_groupware_login(
        page,
        BASE,
        GOOD_ID,
        GOOD_SECRET,
        G,
        deadline=60.0,
        now=ticker.now,
        sleep=sleep,
        ask_otp=lambda _r, _t: None,
    )
    assert page.url == HOME_URL and page.submits == 1


def test_auth01_groupware_requires_input() -> None:
    page = FakeGroupwarePage()
    with pytest.raises(CredentialsRequiredError):
        _login(page, user="", value="")
    assert page.url == ""  # 사이트를 열지도 않는다


def test_auth01_login_all_sends_plain_id_and_common_secret_to_groupware(
    tmp_path: Path, clock: FixedClock
) -> None:
    """그룹웨어 직접 로그인(only): 아이디만 + 공통 암호(위키 전용 암호·도메인 미사용)."""
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
                "only": "groupware",
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
    # 그룹웨어는 일괄 로그인 대상이 아니다(결정기록 0026)
    assert auths[Site.GROUPWARE].calls == 0 and "groupware" not in results
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
    store.set("ui_dashboard_grain", "<script>")  # 허용 값이 아니면 기본값(결정기록 0039)
    with app_client(container) as client:
        page = client.get("/")
        assert page.status_code == 200 and '<script>"' not in page.text
        assert 'data-g="month" class="on"' in page.text
        assert client.get("/api/ui-prefs").json() == {
            "detail_w": None,
            "triage_list_w": None,
            "triage_wide": False,
            "dashboard_grain": None,
        }


def test_auth01_groupware_wrong_input_wins_over_visible_otp_box() -> None:
    """인증 칸이 미리 보여도 번호를 묻기 전에 뜬 일반 알림창은 입력 오류로 끝낸다(보안검토)."""
    page = FakeGroupwarePage("otp_with_alert", good_otp=_otp())
    answers = OtpAnswers(_otp())
    with pytest.raises(LoginFailedError) as info:
        _login(page, ask_otp=answers)
    assert not isinstance(info.value, OtpFailedError)
    assert answers.asks == [] and page.otp_submits == 0


class _Dialog:
    def __init__(self, kind: str, message: str) -> None:
        self.type = kind
        self.message = message
        self.result = ""

    def accept(self) -> None:
        self.result = "accept"

    def dismiss(self) -> None:
        self.result = "dismiss"


class _EventPage:
    def on(self, event: str, handler: Any) -> None:
        self.handler = handler


@pytest.mark.parametrize(
    ("kind", "result"), [("alert", "accept"), ("confirm", "dismiss"), ("prompt", "dismiss")]
)
def test_auth01_groupware_only_alert_otp_notice_is_accepted(kind: str, result: str) -> None:
    """인증번호 안내 글자가 있어도 확인·입력 창은 수락하지 않는다(보안검토)."""
    from security.site_login import PlaywrightFormLoginPage

    page = _EventPage()
    form = PlaywrightFormLoginPage(page, None)
    dialog = _Dialog(kind, "인증번호가 발송되었습니다.")
    page.handler(dialog)
    assert dialog.result == result
    assert form.otp_notice_count == (1 if kind == "alert" else 0)


def test_auth01_describe_log_strips_control_characters() -> None:
    from security.site_login import _printable

    raw = "input #id" + chr(10) + "FAKE LOG" + chr(13) + chr(27) + "[0m"
    assert _printable(raw) == "input #idFAKE LOG[0m"


def test_auth01_batch_login_skips_groupware(tmp_path: Path, clock: FixedClock) -> None:
    """결정기록 0026: 일괄 로그인은 그룹웨어를 시도하지 않는다(인증문자 대기로 막힘 방지).

    그룹웨어는 사용자가 직접(only=groupware) 요청할 때만 로그인한다.
    """
    container = make_container(tmp_path, clock)
    spy = _Recorder()
    container.login.password_authenticators[Site.GROUPWARE] = spy  # type: ignore[index]
    body = {"user_id": "u", "pass" + "word": secrets.token_urlsafe(8)}
    with app_client(container) as client:
        batch = client.post("/api/login/all", json=body).json()["results"]
        assert spy.calls == 0 and "groupware" not in {r["site"] for r in batch}
        other = client.post("/api/login/all", json={**body, "only": "wiki"}).json()["results"]
        assert spy.calls == 0 and "groupware" not in {r["site"] for r in other}
        manual = client.post("/api/login/all", json={**body, "only": "groupware"}).json()
    assert spy.calls == 1 and manual["results"][0]["site"] == "groupware"
