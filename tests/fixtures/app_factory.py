"""테스트용 앱·설정 조립 도우미. 실제 사이트·브라우저에 접속하지 않는다."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.bootstrap import build_container
from app.container import Container
from app.factory import create_app
from app.web_security import LOOPBACK_CLIENTS
from config.settings import Settings
from domain.clock import FixedClock
from security.browser import BrowserRuntime

TEST_PORT = 18080
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


class FakeRawContext:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    """Playwright 대신 쓰는 가짜 백엔드. 임시 폴더에 가짜 프로필 파일을 만든다."""

    instances: list[FakeBackend] = []

    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir
        (temp_dir / "profile").mkdir()
        (temp_dir / "profile" / "Cookies").write_bytes(b"fake-cookie-store")
        self.contexts: list[FakeRawContext] = []
        self.closed = False
        FakeBackend.instances.append(self)

    def new_context(self, headless: bool) -> FakeRawContext:
        context = FakeRawContext()
        self.contexts.append(context)
        return context

    def close(self) -> None:
        self.closed = True


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_port": TEST_PORT,
        "wiki_base_url": "https://wiki.example.invalid",
        "gknowledge_base_url": "https://gk.example.invalid",
        "aiground_base_url": "https://ai.example.invalid",
        "groupware_mail_base_url": "https://gw.example.invalid",
        "db_path": tmp_path / "data" / "test.db",
        "log_dir": tmp_path / "logs",
        "demo_mode": True,
        # 시연용 가상 과거 사례는 넣지 않는다(테스트 기댓값 고정). 필요한 테스트만 켠다
        "demo_seed": False,
        # 데모가 아닐 때도 실제 아웃룩 COM에 연결하지 않도록 그룹웨어(미연동) 경로로 고정한다.
        "mail_source": "groupware",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def make_container(tmp_path: Path, clock: FixedClock, **overrides: Any) -> Container:
    settings = make_settings(tmp_path, **overrides)
    browser = BrowserRuntime(backend_factory=FakeBackend)
    return build_container(settings, clock=clock, browser=browser)


@contextmanager
def app_client(container: Container) -> Iterator[TestClient]:
    # TestClient의 접속 주소는 'testclient'이므로 루프백 목록에 더해 준다.
    app = create_app(
        container,
        run_background_jobs=False,
        trusted_clients=(*LOOPBACK_CLIENTS, "testclient"),
    )
    with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as client:
        client.headers.update({"X-CSRF-Token": container.csrf_token})
        yield client
