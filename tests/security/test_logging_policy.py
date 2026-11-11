"""로그 정책 테스트(AUTH-08)."""

from __future__ import annotations

import io
import logging
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest

from fixtures.synthetic import synthetic_rrn
from security.logging_policy import (
    AUDIT_LOGGER_NAME,
    DROPPED_RECORD_NOTICE,
    FILTER_FAILURE_NOTICE,
    MAX_LOG_MESSAGE_CHARS,
    REMOVED_ATTRIBUTE,
    EventType,
    Site,
    configure_logging,
    log_event,
    uninstall_record_sanitizer,
)


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "logs" / "app.log"
    configure_logging(path)
    yield path
    for handler in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(handler)
        handler.close()
    uninstall_record_sanitizer()


def _read(path: Path) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "message",
    [
        "login password=hunter2",
        "비밀번호: abc123",
        "user_id=hong token=abc",
        "Set-Cookie: JSESSIONID=xyz",
        "Authorization: Bearer abcdefghijk",
    ],
)
def test_auth08_sensitive_keywords_never_written(log_file: Path, message: str) -> None:
    logging.getLogger("test").warning(message)
    content = _read(log_file)
    assert DROPPED_RECORD_NOTICE in content
    for fragment in ("hunter2", "abc123", "hong", "xyz", "abcdefghijk", "password="):
        assert fragment not in content


def test_auth08_args_formatting_also_filtered(log_file: Path) -> None:
    logging.getLogger("test").info("입력값 %s", "password=hunter2")
    assert "hunter2" not in _read(log_file)


def test_auth08_personal_data_masked_in_message(log_file: Path) -> None:
    rrn = synthetic_rrn()
    logging.getLogger("test").info("처리 대상 %s 연락처 010-1234-5678", rrn)
    content = _read(log_file)
    assert rrn not in content
    assert "010-1234-5678" not in content
    assert "처리 대상" in content


def test_auth08_exception_text_filtered(log_file: Path) -> None:
    try:
        raise RuntimeError("login failed password=hunter2")
    except RuntimeError:
        logging.getLogger("test").exception("오류")
    content = _read(log_file)
    assert "hunter2" not in content
    assert "Traceback" not in content


def test_auth08_audit_event_has_only_allowed_fields(log_file: Path) -> None:
    log_event(EventType.LOGIN, Site.WIKI, success=False)
    content = _read(log_file)
    line = next(line for line in content.splitlines() if AUDIT_LOGGER_NAME in line)
    assert line.endswith("event=login site=wiki result=failure")
    # 시각(asctime)이 앞에 붙는다: YYYY-MM-DD
    assert line[:4].isdigit() and line[4] == "-"


@pytest.mark.parametrize("event", list(EventType))
def test_auth08_every_audit_event_survives_filter(log_file: Path, event: EventType) -> None:
    log_event(event, Site.APP, success=True)
    assert f"event={event.value} site=app result=success" in _read(log_file)


# --- 필터 우회 경로 회귀(보안 검토 H-2, M-5) ---------------------------------


def _isolated_logger(name: str, propagate: bool) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(logging.StreamHandler(stream))
    logger.propagate = propagate
    return logger, stream


@pytest.mark.parametrize("propagate", [True, False])
def test_auth08_logger_with_own_handler_is_sanitized(log_file: Path, propagate: bool) -> None:
    value = secrets.token_hex(6)
    logger, stream = _isolated_logger(f"thirdparty.{propagate}", propagate)
    try:
        logger.warning("password=%s", value)
        logger.warning("연락처 010-1234-5678")
    finally:
        logger.handlers.clear()
    output = stream.getvalue()
    assert value not in output
    assert "010-1234-5678" not in output
    assert value not in _read(log_file)


@pytest.mark.parametrize(
    "template", ["id={v}", "ID: {v}", "PW: {v}", "패스워드 {v}", "credential {v}", "sid={v}"]
)
def test_auth08_more_blocked_keywords(log_file: Path, template: str) -> None:
    value = secrets.token_hex(6)
    logging.getLogger("test").warning(template.format(v=value))
    assert value not in _read(log_file)


def test_auth08_extra_attributes_removed(log_file: Path) -> None:
    value = secrets.token_hex(6)
    # pytest도 루트에 수집 핸들러를 붙이므로 우리 파일 핸들러를 정확히 고른다.
    handler = next(h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler))
    handler.setFormatter(logging.Formatter("%(message)s %(user)s"))
    logging.getLogger("test").info("요청 처리", extra={"user": f"password={value}"})
    content = _read(log_file)
    assert value not in content
    assert REMOVED_ATTRIBUTE in content


def test_auth08_oversized_message_does_not_raise(log_file: Path) -> None:
    logging.getLogger("test").info("x" * (MAX_LOG_MESSAGE_CHARS * 200))
    content = _read(log_file)
    assert FILTER_FAILURE_NOTICE not in content
    assert len(content) < MAX_LOG_MESSAGE_CHARS * 2


def test_auth08_uvicorn_default_loggers_are_sanitized(log_file: Path) -> None:
    import logging.config

    from uvicorn.config import LOGGING_CONFIG

    value = secrets.token_hex(6)
    logging.config.dictConfig(LOGGING_CONFIG)
    stream = io.StringIO()
    access = logging.getLogger("uvicorn.access")
    access.handlers[0].setStream(stream)  # type: ignore[attr-defined]
    access.info('%s - "%s %s HTTP/1.1" %d', "127.0.0.1", "GET", f"/x?token={value}", 200)
    assert value not in stream.getvalue()
