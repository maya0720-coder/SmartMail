"""그룹웨어 인증문자 입력 전달 테스트(AUTH-01·09, 결정기록 0020).

실제 사이트·브라우저에 접속하지 않는다. 인증문자 번호는 실행할 때마다 무작위로 만든다.
"""

from __future__ import annotations

import secrets
import threading
from pathlib import Path

from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container
from security.site_login import LoginProgress


def _otp() -> str:
    return str(secrets.randbelow(900_000) + 100_000)


def _wait_until_asked(progress: LoginProgress) -> None:
    for _ in range(500):
        if progress.snapshot()["otp_waiting"]:
            return
        threading.Event().wait(0.01)
    raise AssertionError("번호 요청 상태가 되지 않았다")


def _ask_in_background(
    progress: LoginProgress, retry: bool = False
) -> tuple[threading.Thread, list[str | None]]:
    got: list[str | None] = []
    worker = threading.Thread(target=lambda: got.append(progress.ask_otp(retry, 5.0)))
    worker.start()
    _wait_until_asked(progress)
    return worker, got


def test_auth09_otp_handed_over_once_and_cleared() -> None:
    progress = LoginProgress()
    worker, got = _ask_in_background(progress, retry=True)
    assert progress.snapshot()["otp_retry"] is True
    code = _otp()
    assert progress.submit_otp(code) is True
    worker.join(5)
    assert got == [code]
    snap = progress.snapshot()
    assert snap["otp_waiting"] is False and code not in str(snap)  # 상태에 번호가 남지 않음
    assert progress.submit_otp(_otp()) is False  # 기다리지 않을 때는 받지 않음


def test_auth09_otp_cancel_and_timeout_return_none() -> None:
    progress = LoginProgress()
    worker, got = _ask_in_background(progress)
    progress.cancel_otp()
    worker.join(5)
    assert got == [None]
    assert progress.ask_otp(False, 0.05) is None  # 시간 초과
    assert progress.snapshot()["otp_waiting"] is False


def test_auth09_otp_api_validates_and_never_echoes(tmp_path: Path, clock: FixedClock) -> None:
    """번호 API: 기다리는 중이 아니면 409, 형식이 틀리면 400. 응답에 번호를 담지 않는다."""
    container = make_container(tmp_path, clock)
    code = _otp()
    with app_client(container) as client:
        idle = client.post("/api/login/otp", json={"code": code})
        assert idle.status_code == 409 and code not in idle.text
        for bad in ["12", "12 34", "<script>", "1" * 13]:
            assert client.post("/api/login/otp", json={"code": bad}).status_code in (400, 422)
        assert client.post("/api/login/otp?code=" + code, json={"code": code}).status_code == 400
        assert client.post("/api/login/otp", json={"cancel": True}).status_code == 200

        progress = container.login_progress
        got: list[str | None] = []
        worker = threading.Thread(target=lambda: got.append(progress.ask_otp(False, 5.0)))
        worker.start()
        _wait_until_asked(progress)
        status = client.get("/api/login/progress").json()
        assert status["otp_waiting"] is True
        done = client.post("/api/login/otp", json={"code": code})
        worker.join(5)
    assert done.status_code == 200 and code not in done.text
    assert got == [code]
