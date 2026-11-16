"""그룹웨어 보안문자(CAPTCHA) 로그인 테스트(AUTH-01·09, 결정기록 0025).

실제 사이트·브라우저에 접속하지 않는다. 화면은 상태를 흉내 내는 가짜 페이지로 재현하고,
아이디·암호·보안문자 값은 실행할 때마다 무작위로 만든다.
"""

from __future__ import annotations

import logging
import secrets
import threading
from pathlib import Path
from typing import Any

import pytest

from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container
from security.captcha_channel import CaptchaChannel
from security.credentials import LoginFailedError
from security.groupware_captcha import (
    MAX_IMAGE_BYTES,
    REFRESH_TOKEN,
    CaptchaFailedError,
    ErrorCategory,
    classify_message,
    normalize,
    parse_policy,
)
from security.login_flows import load_login_fields, run_groupware_login

G = load_login_fields().groupware
BASE = "https://gw.example.invalid/"
LOGIN_URL = BASE + "WebSite/Login.aspx"
HOME_URL = BASE + "WebSite/Portal/Main.aspx"
GOOD_ID = "tester01"
GOOD_SECRET = secrets.token_urlsafe(12)
PNG = b"\x89PNG\r\n\x1a\n" + secrets.token_bytes(32)


def _code() -> str:
    return secrets.token_hex(3)  # 영문·숫자 6자


class CaptchaPage:
    """보안문자가 있는 그룹웨어 로그인 화면 흉내.

    enter_works=False면 Enter 제출이 먹지 않고 제출 버튼 클릭만 제출된다.
    값이 틀리면 알림창(보안문자 불일치)이 뜨고 화면이 새로 불러와져 새 이미지가 나온다.
    """

    def __init__(
        self,
        good: str,
        *,
        enter_works: bool = True,
        keeps_secret: bool = True,
        fail_mode: str = "captcha",
    ) -> None:
        self.good, self.enter_works, self.keeps_secret = good, enter_works, keeps_secret
        # fail_mode: captcha | cred_dialog(암호 오류 알림창) | cred_area | other_dialog | silent
        self.fail_mode = fail_mode
        self.area: ErrorCategory | None = None
        self.url = LOGIN_URL
        self.typed: dict[str, str] = {}
        self.fills: list[tuple[str, str]] = []
        self.real_box_shown = False
        self.src = "captcha-1"
        self.submits = 0
        self.refresh_clicks = 0
        self.button_clicks = 0
        self.dialog_count = 0
        self.otp_notice_count = 0
        self.captcha_notice_count = 0
        self.navigation_count = 0
        self.last_error_category = ErrorCategory.OTHER
        self.png = PNG
        self.hidden = ["csrf", "captchaKey"]

    def goto(self, url: str, timeout: float = 0) -> None:
        self.url = LOGIN_URL

    def visible(self, selector: str) -> bool:
        if selector == G.user:
            return "Login.aspx" in self.url
        if selector == G.concealed_placeholder:
            return not self.real_box_shown
        if selector in (G.captcha_image, G.captcha_input):
            return "Login.aspx" in self.url
        if selector == G.captcha_refresh:
            return True
        if selector == G.submit:
            return not self.enter_works
        return False

    def fill(self, selector: str, value: str) -> None:
        self.typed[selector] = value
        self.fills.append((selector, value))

    def click(self, selector: str) -> None:
        if selector == G.concealed_placeholder:
            self.real_box_shown = True
        elif selector == G.captcha_refresh:
            self.refresh_clicks += 1
            self.src = f"captcha-r{self.refresh_clicks}"
        elif selector == G.submit:
            self.button_clicks += 1
            self._submit()

    def press(self, selector: str, key: str) -> None:
        assert key == "Enter"
        if self.enter_works:
            self._submit()

    def _submit(self) -> None:
        self.submits += 1
        ok = (
            self.typed.get(G.user) == GOOD_ID
            and self.typed.get(G.concealed_input) == GOOD_SECRET
            and self.typed.get(G.captcha_input) == self.good
        )
        if ok:
            self.url = HOME_URL
            return
        if self.fail_mode == "cred_dialog":
            self.dialog_count += 1
            self.last_error_category = ErrorCategory.CREDENTIALS
            return
        if self.fail_mode == "other_dialog":
            self.dialog_count += 1
            self.last_error_category = ErrorCategory.OTHER
            return
        if self.fail_mode == "cred_area":
            self.area = ErrorCategory.CREDENTIALS
            return
        if self.fail_mode == "silent":
            return
        self.captcha_notice_count += 1  # 보안문자 불일치 안내
        self.navigation_count += 1  # 화면이 새로 불러와짐 = 새 이미지
        self.src = f"captcha-n{self.navigation_count}"
        self.typed.pop(G.captcha_input, None)
        if not self.keeps_secret:
            self.typed.pop(G.concealed_input, None)

    def text(self, selector: str) -> str:
        return ""

    def get_json(self, url: str) -> Any:
        return None

    def capture(self, selector: str) -> bytes | None:
        return self.png

    def attribute(self, selector: str, name: str) -> str:
        return self.src

    def has_value(self, selector: str) -> bool:
        return bool(self.typed.get(selector))

    def hidden_names(self) -> list[str]:
        return self.hidden

    def error_category(self, selector: str) -> ErrorCategory | None:
        return self.area


class Ticker:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class Answers:
    """사용자가 넣는 보안문자를 차례로 돌려준다. 받은 요청(이미지·재입력 여부)을 기록한다."""

    def __init__(self, *codes: str | None, on_ask: Any = None) -> None:
        self._codes = list(codes)
        self.images: list[bytes] = []
        self.retries: list[bool] = []
        self._on_ask = on_ask

    def __call__(self, png: bytes, retry: bool, timeout: float) -> str | None:
        assert timeout > 0
        self.images.append(png)
        self.retries.append(retry)
        if self._on_ask:
            self._on_ask(len(self.retries))
        return self._codes.pop(0) if self._codes else None


def _login(page: CaptchaPage, ask: Any) -> None:
    ticker = Ticker()
    run_groupware_login(
        page,
        BASE,
        GOOD_ID,
        GOOD_SECRET,
        G,
        deadline=600.0,
        now=ticker.now,
        sleep=ticker.sleep,
        ask_otp=lambda _r, _t: None,
        ask_captcha=ask,
    )


def test_auth01_captcha_shown_entered_and_login_succeeds() -> None:
    """보안문자 표시 → 입력(공백 제거) → 같은 화면에서 제출 → 성공. 암호는 한 번만 입력."""
    code = _code()
    page = CaptchaPage(code)
    answers = Answers(f"  {code[:3]} {code[3:]}\t")
    _login(page, answers)
    assert page.url == HOME_URL and page.submits == 1
    assert answers.images == [PNG] and answers.retries == [False]
    assert [v for s, v in page.fills if s == G.concealed_input] == [GOOD_SECRET]
    assert page.typed[G.captcha_input] == code


def test_auth01_captcha_wrong_then_new_image_then_success() -> None:
    """오답이면 새 이미지로 다시 묻는다. 암호칸은 다시 채우지 않고 제출은 보안문자 칸에서 한다."""
    code = _code()
    page = CaptchaPage(code)
    answers = Answers("zz" + code, code)
    _login(page, answers)
    assert page.url == HOME_URL and page.submits == 2
    assert answers.retries == [False, True]
    assert [v for s, v in page.fills if s == G.concealed_input] == [GOOD_SECRET]


def test_auth01_captcha_gives_up_after_max_tries() -> None:
    """계속 틀리면 정해진 횟수만 제출하고 중단한다(계정 잠금 방지)."""
    policy = parse_policy(G.captcha_case, G.captcha_length, G.captcha_max_tries)
    page = CaptchaPage(_code())
    with pytest.raises(CaptchaFailedError) as info:
        _login(page, Answers(*["zz" + _code() for _ in range(10)]))
    assert page.submits == 1 + policy.max_tries and "zz" not in str(info.value)  # 최초 + 재제출
    assert isinstance(info.value, LoginFailedError)


def test_auth01_captcha_image_changed_while_waiting_asks_again() -> None:
    """사용자가 보는 동안 이미지(src)가 바뀌면(재발급) 값을 쓰지 않고 새 이미지로 다시 묻는다."""
    code = _code()
    page = CaptchaPage(code)

    def reload_once(count: int) -> None:
        if count == 1:
            page.src = "captcha-reissued"

    answers = Answers(_code(), code, on_ask=reload_once)
    _login(page, answers)
    assert page.url == HOME_URL and page.submits == 1
    assert answers.retries == [False, True]  # 낡은 값은 채우지 않았다
    assert [v for s, v in page.fills if s == G.captcha_input] == [code]


def test_auth01_captcha_refresh_button_requests_new_image() -> None:
    code = _code()
    page = CaptchaPage(code)
    _login(page, Answers(REFRESH_TOKEN, code))
    assert page.refresh_clicks == 1 and page.url == HOME_URL


def test_auth01_captcha_submit_button_fallback_when_enter_does_nothing() -> None:
    """Enter 제출에 변화가 없으면 제출 버튼을 누른다."""
    code = _code()
    page = CaptchaPage(code, enter_works=False)
    _login(page, Answers(code))
    assert page.button_clicks == 1 and page.url == HOME_URL


def test_auth01_captcha_stops_when_page_cleared_password_on_retry() -> None:
    """재입력 때 화면이 암호칸을 비웠으면 빈 암호를 제출하지 않고 중단한다."""
    page = CaptchaPage(_code(), keeps_secret=False)
    with pytest.raises(CaptchaFailedError):
        _login(page, Answers("zz" + _code(), _code()))
    assert page.submits == 1


@pytest.mark.parametrize("size", [0, MAX_IMAGE_BYTES + 1])
def test_auth01_captcha_rejects_missing_or_oversized_image(size: int) -> None:
    page = CaptchaPage(_code())
    page.png = b"x" * size
    with pytest.raises(CaptchaFailedError):
        _login(page, Answers(_code()))
    assert page.submits == 0


def test_auth01_captcha_cancel_or_no_handler_submits_nothing() -> None:
    page = CaptchaPage(_code())
    with pytest.raises(CaptchaFailedError):
        _login(page, Answers(None))
    with pytest.raises(CaptchaFailedError):
        _login(CaptchaPage(_code()), None)
    assert page.submits == 0


def test_auth09_captcha_logs_have_no_values_or_image(caplog: pytest.LogCaptureFixture) -> None:
    """로그에는 hidden 입력 개수·이름만 남고, 보안문자 값·이미지·암호는 없다."""
    code = _code()
    page = CaptchaPage(code)
    with caplog.at_level(logging.DEBUG):
        _login(page, Answers("zz" + code, code))
    text = caplog.text
    assert "hidden 입력 2개" in text and "csrf" in text
    for secret in (code, GOOD_SECRET, GOOD_ID, PNG.hex(), repr(PNG)):
        assert secret not in text


@pytest.mark.parametrize(
    ("raw", "case", "expected"),
    [
        ("  aB 3 d9 ", "keep", "aB3d9"),
        ("aB3d9", "upper", "AB3D9"),
        ("aB3d9", "lower", "ab3d9"),
        ("abc", "keep", None),  # 너무 짧음
        ("a" * 9, "keep", None),  # 너무 김
        ("ab-12", "keep", None),  # 영문·숫자 아님
        ("<b>12", "keep", None),
        ("가나다라", "keep", None),
        ("", "keep", None),
    ],
)
def test_auth09_captcha_normalize(raw: str, case: str, expected: str | None) -> None:
    assert normalize(raw, parse_policy(case, "4-8", "3")) == expected


@pytest.mark.parametrize(
    ("case", "length", "tries"),
    [("x", "4-8", "3"), ("keep", "8-4", "3"), ("keep", "a", "3"), ("keep", "4-8", "4")],
)
def test_auth09_captcha_policy_config_errors_fail_fast(case: str, length: str, tries: str) -> None:
    from config.settings import ConfigError

    with pytest.raises(ConfigError):
        parse_policy(case, length, tries)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("보안문자가 일치하지 않습니다.", ErrorCategory.CAPTCHA),
        ("Captcha is wrong", ErrorCategory.CAPTCHA),
        ("인증번호가 발송되었습니다.", ErrorCategory.OTP),
        ("아이디 또는 비밀번호가 틀렸습니다.", ErrorCategory.CREDENTIALS),
        ("서버 오류", ErrorCategory.OTHER),
    ],
)
def test_auth01_error_message_classification(message: str, expected: ErrorCategory) -> None:
    assert classify_message(message) is expected


def test_auth09_captcha_dialog_counted_separately_by_page() -> None:
    """보안문자 불일치 알림창은 일반 오류(아이디·암호)로 세지 않고 따로 센다."""
    from security.site_login import PlaywrightFormLoginPage

    class Dlg:
        type = "alert"

        def __init__(self, message: str) -> None:
            self.message = message
            self.done = ""

        def accept(self) -> None:
            self.done = "accept"

        def dismiss(self) -> None:
            self.done = "dismiss"

    class Ev:
        def on(self, event: str, handler: Any) -> None:
            if event == "dialog":
                self.handler = handler

    ev = Ev()
    form = PlaywrightFormLoginPage(ev, None)
    ev.handler(Dlg("보안문자가 일치하지 않습니다."))
    assert (form.captcha_notice_count, form.dialog_count) == (1, 0)
    ev.handler(Dlg("아이디 또는 비밀번호 오류"))
    assert (form.captcha_notice_count, form.dialog_count) == (1, 1)
    assert form.last_error_category is ErrorCategory.CREDENTIALS


def test_auth09_captcha_channel_hands_over_once_and_discards_image() -> None:
    channel = CaptchaChannel()
    got: list[str | None] = []
    worker = threading.Thread(target=lambda: got.append(channel.ask(PNG, True, 5.0)))
    worker.start()
    for _ in range(500):
        if channel.snapshot()["captcha_waiting"]:
            break
        threading.Event().wait(0.01)
    snap = channel.snapshot()
    assert snap["captcha_retry"] is True and snap["captcha_seq"] == 1
    url = channel.take_image_data_url()
    assert url is not None and url.startswith("data:image/png;base64,")
    assert channel.take_image_data_url() is None  # 한 번 가져가면 서버에서 버린다
    code = _code()
    assert channel.submit(code) is True and channel.submit(code) is False
    worker.join(5)
    assert got == [code] and code not in str(channel.snapshot())


def test_auth09_captcha_api_validates_no_store_and_conflict(
    tmp_path: Path, clock: FixedClock
) -> None:
    """API: 기다리는 중이 아니면 409, 형식 오류 400, 응답에 값을 담지 않고 no-store."""
    container = make_container(tmp_path, clock)
    code = _code()
    with app_client(container) as client:
        idle = client.post("/api/login/captcha", json={"code": code})
        assert idle.status_code == 409 and code not in idle.text
        assert idle.headers["cache-control"] == "no-store"
        assert client.post("/api/login/captcha/image", json={}).status_code == 409
        assert client.get("/api/login/captcha").status_code in (404, 405)  # GET으로는 못 가져감
        assert client.post("/api/login/captcha", json={"refresh": True}).status_code == 409
        for bad in ["ab", "a b-c", "<script>", "x" * 41]:
            assert client.post("/api/login/captcha", json={"code": bad}).status_code in (400, 422)
        assert (
            client.post("/api/login/captcha?code=" + code, json={"code": code}).status_code == 400
        )
        assert client.post("/api/login/captcha", json={"cancel": True}).status_code == 200

        channel = container.login_progress.captcha
        got: list[str | None] = []
        worker = threading.Thread(target=lambda: got.append(channel.ask(PNG, False, 5.0)))
        worker.start()
        for _ in range(500):
            if channel.snapshot()["captcha_waiting"]:
                break
            threading.Event().wait(0.01)
        status = client.get("/api/login/progress")
        assert status.json()["kind"] == "captcha" and status.headers["cache-control"] == "no-store"
        image = client.post("/api/login/captcha/image", json={})
        assert image.json()["image"].startswith("data:image/png;base64,")
        assert image.headers["cache-control"] == "no-store"
        assert (
            client.post("/api/login/captcha/image", json={}).status_code == 409
        )  # 두 번째는 받을 수 없다
        done = client.post("/api/login/captcha", json={"code": f" {code[:3]} {code[3:]} "})
        worker.join(5)
    assert done.status_code == 200 and code not in done.text
    assert got == [code]  # 공백이 제거된 값


@pytest.mark.parametrize("mode", ["cred_dialog", "cred_area", "other_dialog", "silent"])
def test_auth09_captcha_not_resubmitted_for_password_error_other_or_timeout(mode: str) -> None:
    """암호 오류·분류 불명·시간 경과는 보안문자 재제출 없이 중단한다(암호 1회 제출)."""
    page = CaptchaPage(_code(), fail_mode=mode)
    answers = Answers(*[_code() for _ in range(5)])
    with pytest.raises(LoginFailedError) as info:
        _login(page, answers)
    assert not isinstance(info.value, CaptchaFailedError)
    assert page.submits == 1 and answers.retries == [False]


def test_auth09_captcha_resubmit_cap_is_conservative() -> None:
    policy = parse_policy(G.captcha_case, G.captcha_length, G.captcha_max_tries)
    assert policy.max_tries <= 2


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/WebSite/Login.aspx;jsessionid=ABC123SECRET", "/WebSite/Login.aspx"),
        ("/a;x=1/b", "/a"),
        ("/한글/ab<>c%0Ax", "//abc0Ax"),
    ],
)
def test_auth09_describe_path_strips_path_params(path: str, expected: str) -> None:
    from security.site_login import safe_path

    assert safe_path(path) == expected
