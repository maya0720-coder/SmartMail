"""트리아지 개인정보 테스트(MAIL-08, TODO-01, 결정기록 0015).

이유는 코드값만 저장하고, 근거 메일은 저장하지 않는다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.bootstrap import build_container
from domain.clock import FixedClock
from domain.models import REASON_CODES
from fixtures.app_factory import FakeBackend, app_client, make_settings
from security.browser import BrowserRuntime
from security.logging_policy import configure_logging, uninstall_record_sanitizer
from storage.db import Database


def _file_container(tmp_path: Path, clock: FixedClock):  # type: ignore[no-untyped-def]
    settings = make_settings(tmp_path)
    return build_container(
        settings,
        clock=clock,
        db=Database(settings.db_path),
        browser=BrowserRuntime(backend_factory=FakeBackend),
    )


# ④ 이유는 코드값만 저장(문장·주소 없음)
def test_todo01_reasons_saved_as_codes_only(tmp_path: Path, clock: FixedClock) -> None:
    container = _file_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
    rows = container.db.query("SELECT reasons, confidence FROM task")
    assert rows
    for row in rows:
        codes = [c for c in (row["reasons"] or "").split(",") if c]
        assert codes and all(code in REASON_CODES for code in codes)
        assert row["confidence"] is not None and 0 <= row["confidence"] <= 1
    dumped = repr([tuple(r) for r in container.db.query("SELECT * FROM task")])
    assert "@" not in dumped  # 메일 주소 없음
    container.db.close()


# ⑤ 근거 메일(context)은 DB·로그에 남지 않는다
def test_mail08_context_not_persisted(tmp_path: Path, clock: FixedClock) -> None:
    container = _file_container(tmp_path, clock)
    log_file = tmp_path / "logs" / "app.log"
    configure_logging(log_file)
    seen: list[str] = []
    try:
        with app_client(container) as client:
            client.post("/api/mail/collect", json={})
            for task in client.get("/api/tasks").json():
                data = client.get(f"/api/tasks/{task['id']}/context").json()
                assert data["missing"] is False and data["lines"]
                seen.extend(line for line in data["lines"] if len(line) >= 8)
                assert (
                    data["highlight"] is None
                    or data["highlight"]["end"] > data["highlight"]["start"]
                )
    finally:
        uninstall_record_sanitizer()
        for handler in list(logging.getLogger().handlers):
            if getattr(handler, "baseFilename", "").startswith(str(tmp_path)):
                logging.getLogger().removeHandler(handler)
                handler.close()
    container.db.close()
    assert seen
    stored = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    leaked = [line for line in seen if line.encode() in stored]
    assert leaked == []
