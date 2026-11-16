"""G-Knowledge 백그라운드 등록 상태를 끝날 때까지 기다리는 테스트 도우미(결정기록 0035)."""

from __future__ import annotations

import time
from typing import Any

TERMINAL = {"requested", "failed_unsent", "unknown"}


def wait_gk(client: Any, task_id: int, timeout: float = 5.0) -> dict[str, Any]:
    """상태 API를 짧게 반복 조회해 끝난 상태(완료·실패·확인 필요)를 돌려준다."""
    deadline = time.monotonic() + timeout
    while True:
        found = client.get("/api/gk/status", params={"ids": str(task_id)}).json()
        status = found.get(str(task_id))
        if status and status["state"] in TERMINAL:
            return dict(status)
        if time.monotonic() > deadline:
            raise AssertionError(f"등록 상태가 끝나지 않았습니다: {status}")
        time.sleep(0.02)
