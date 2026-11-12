"""메일 수집 후 업무 반영(MAIL-04, TODO-01·04·05). 자동 수집 작업과 화면 API가 함께 쓴다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.container import Container
from domain.clock import KST
from domain.mail import CollectorUnavailableError
from domain.mail_service import CollectionResult
from security.logging_policy import EventType, Site, log_event

logger = logging.getLogger(__name__)


def _site(container: Container) -> Site:
    return Site.OUTLOOK if container.mail.collector.source == "outlook" else Site.GROUPWARE


def collect_and_ingest(
    container: Container, collect: Callable[[], CollectionResult]
) -> dict[str, Any]:
    """수집 함수를 실행하고 결과 스레드를 업무로 반영한다. 수집 실패는 그대로 던진다."""
    with container.mail_lock:
        try:
            result = collect()
        except CollectorUnavailableError:
            log_event(EventType.MAIL_COLLECT, _site(container), success=False)
            raise
        log_event(EventType.MAIL_COLLECT, _site(container), success=True)
        ingest = container.tasks.ingest(result.threads)
    return {
        "suggested": len(ingest.suggested),
        "linked": ingest.linked,
        "reply_detected": ingest.reply_detected,
        "threads": len(result.threads),
        "new_messages": result.new_messages,
        "excluded_messages": result.excluded_messages,
        "since": result.since.astimezone(KST).isoformat(),
        "until": result.until.astimezone(KST).isoformat(),
        "initial": result.initial,
    }


def background_sync(container: Container) -> None:
    """주기 작업용 자동 수집. 수집 경로를 쓸 수 없으면 경고만 남기고 다음 주기에 다시 시도한다."""
    try:
        collect_and_ingest(container, container.mail.sync)
    except CollectorUnavailableError:
        logger.warning("자동 메일 수집 실패: 수집 경로를 쓸 수 없습니다")
