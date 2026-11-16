"""로그 정책(AUTH-08, 기획서 5장 감사).

- 로그에는 이벤트 종류, 시각, 성공/실패, 대상 사이트 이름만 남긴다(`log_event`).
- 모든 레코드는 생성 시점(LogRecord 팩토리)에 정화된다. 따라서 로거별 핸들러,
  propagate=False 로거(uvicorn 등), 설정 전 lastResort 출력도 같은 정책을 거친다.
  정화: 메시지·예외를 마스킹하고, ID·암호·토큰·세션·쿠키 키워드가 들어 있는 레코드는
  내용을 대체 문구로 바꾼다(원문은 남지 않는다).
- 핸들러에도 같은 필터를 붙여, extra로 붙은 비표준 속성까지 제거한다(이중 방어).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from security.masking import mask

AUDIT_LOGGER_NAME: Final = "smartmail.audit"
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s %(message)s"

# 로그 메시지 최대 길이(문자). 넘는 부분은 잘라낸 뒤 마스킹한다.
MAX_LOG_MESSAGE_CHARS: Final = 20_000

# 이 키워드가 들어간 레코드는 내용을 기록하지 않는다(마스킹 누락 대비 이중 차단).
_BLOCKED_KEYWORDS = re.compile(
    r"password|passwd|pwd|(?<![A-Za-z0-9])(?:pw|p/w|id|sid)(?![A-Za-z0-9])"
    r"|패스워드|비밀번호|비번|암호|아이디|계정"
    r"|user_?id|login_?id|username|credential"
    r"|token|토큰|secret|authorization|bearer|api[_\-]?key|private[_\-]?key"
    r"|session|세션|cookie|쿠키|jsessionid",
    re.IGNORECASE,
)

DROPPED_RECORD_NOTICE: Final = "민감 키워드가 포함되어 로그 내용을 기록하지 않았습니다"
FILTER_FAILURE_NOTICE: Final = "로그 정화 중 오류가 나서 내용을 기록하지 않았습니다"
REMOVED_ATTRIBUTE: Final = "[제거됨]"

_STANDARD_ATTRS: Final = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    | {"message", "asctime", "taskName", "_smartmail_sanitized"}
)


class Site(StrEnum):
    """로그·감사 대상 사이트."""

    APP = "app"
    OUTLOOK = "outlook"
    GROUPWARE = "groupware"
    WIKI = "wiki"
    GKNOWLEDGE = "g-knowledge"
    AIGROUND = "ai-ground"


class EventType(StrEnum):
    """로그·감사 이벤트 종류. 값에 차단 키워드(session 등)가 들어가지 않게 한다."""

    APP_START = "app_start"
    APP_STOP = "app_stop"
    LOGIN = "login"
    LOGOUT = "logout"
    IDLE_TIMEOUT = "idle_timeout"
    MAIL_COLLECT = "mail_collect"
    WIKI_POST = "wiki_post"
    WIKI_READ = "wiki_read"  # 위키 양식 읽기(결정기록 0037)
    GK_REGISTER = "gk_register"
    GK_SEARCH = "gk_search"
    AI_CALL = "ai_call"
    RETENTION_PURGE = "retention_purge"
    DISCONNECT = "disconnect"  # 사이트 연결이 끝남(사유는 EndReason, 결정기록 0031)


class EndReason(StrEnum):
    """사이트 연결이 끝난 사유(결정기록 0031). 로그·연결 상태 API에 그대로 쓴다.

    값에 차단 키워드(session 등)가 들어가지 않게 한다.
    """

    IDLE_TIMEOUT = "idle_timeout"  # 유휴 시간 초과(실행 중 유지를 끈 경우만)
    CASCADE = "cascade"  # 함께 쓰던 공용 컨텍스트가 다른 사이트 때문에 닫힘
    REPLACED = "replaced"  # 같은 사이트 재로그인으로 교체됨
    SITE_EXPIRED = "site_expired"  # 사이트 쪽에서 로그인이 끝남(연결 유지 점검이 감지)
    LOGOUT = "logout"  # 사용자의 명시적 로그아웃
    APP_STOP = "app_stop"  # 앱 종료


def _render(record: logging.LogRecord) -> str:
    try:
        message = record.getMessage()
    except (TypeError, ValueError):
        # 포맷 인자가 맞지 않는 레코드는 인자를 버리고 형식 문자열만 쓴다.
        message = str(record.msg)
    if record.exc_info:
        message = f"{message}\n{logging.Formatter().formatException(record.exc_info)}"
    elif record.exc_text:
        message = f"{message}\n{record.exc_text}"
    if record.stack_info:
        message = f"{message}\n{record.stack_info}"
    return message


def sanitize_text(message: str) -> str:
    """로그 문자열 하나를 정책에 맞게 정화한다(차단 또는 마스킹)."""
    message = message[:MAX_LOG_MESSAGE_CHARS]
    if _BLOCKED_KEYWORDS.search(unicodedata.normalize("NFKC", message)):
        return DROPPED_RECORD_NOTICE
    return mask(message).value


def sanitize_record(record: logging.LogRecord) -> None:
    """레코드의 메시지·예외를 정화하고 원문 속성을 지운다. 실패하면 안전한 문구로 바꾼다."""
    if getattr(record, "_smartmail_sanitized", False):
        return
    try:
        message = sanitize_text(_render(record))
    except Exception:  # noqa: BLE001 - 로깅 실패가 호출자에게 전파되지 않게 막는다.
        message = FILTER_FAILURE_NOTICE
    record.msg = message
    record.args = None
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None
    record._smartmail_sanitized = True


class SensitiveLogFilter(logging.Filter):
    """핸들러용 필터: 정화하고, extra로 붙은 비표준 속성 값을 지운다."""

    def filter(self, record: logging.LogRecord) -> bool:
        sanitize_record(record)
        for key in list(record.__dict__):
            if key not in _STANDARD_ATTRS:
                record.__dict__[key] = REMOVED_ATTRIBUTE
        return True


_original_factory: Callable[..., logging.LogRecord] | None = None


def install_record_sanitizer() -> None:
    """LogRecord 팩토리를 감싸 모든 로거의 레코드를 생성 시점에 정화한다(중복 설치 안전)."""
    global _original_factory
    if _original_factory is not None:
        return
    original = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original(*args, **kwargs)
        sanitize_record(record)
        return record

    _original_factory = original
    logging.setLogRecordFactory(factory)


def uninstall_record_sanitizer() -> None:
    """테스트 정리용: 원래 팩토리로 되돌린다."""
    global _original_factory
    if _original_factory is not None:
        logging.setLogRecordFactory(_original_factory)
        _original_factory = None


def log_event(event: EventType, site: Site, success: bool, reason: EndReason | None = None) -> None:
    """감사 로그를 '이벤트 종류, 사이트, 성공/실패(, 종료 사유)'만으로 기록한다.

    시각은 포맷터가 붙인다. 사유는 정해진 코드 값만 받는다(자유 문자열 없음).
    """
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    result = "success" if success else "failure"
    if reason is None:
        logger.info("event=%s site=%s result=%s", event.value, site.value, result)
    else:
        logger.info(
            "event=%s site=%s result=%s reason=%s", event.value, site.value, result, reason.value
        )


def configure_logging(log_file: Path | None = None, level: int = logging.INFO) -> None:
    """레코드 정화기를 설치하고 루트 로거에 필터가 붙은 핸들러를 설정한다.

    uvicorn 등 자체 핸들러를 쓰는 로거도 레코드 팩토리 단계에서 정화된다.
    uvicorn 실행 시에는 `log_config=None`으로 루트 설정을 따르게 하는 것을 권장한다.
    """
    install_record_sanitizer()
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    formatter = logging.Formatter(LOG_FORMAT)
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveLogFilter())
        root.addHandler(handler)
    root.setLevel(level)
