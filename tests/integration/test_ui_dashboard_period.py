"""대시보드 기간 필터 화면 테스트(A1, MET·TODO-03, 결정기록 0039).

로컬 127.0.0.1 데모 서버만 쓴다. 업무는 합성 제목으로 직접 만든다.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from domain.models import Task, TaskSource, TaskStatus
from fixtures.ui_server import chromium, demo_server
from storage.repositories import SqliteTaskRepository

# 화면 목록(dismissed 제외)과 같은 규칙으로 서버에서 다시 센다
COUNT = (
    "(q) => fetch('/api/tasks' + q).then(r => r.json())"
    ".then(a => a.filter(t => t.status !== 'dismissed').length)"
)
DONUT = "() => Number(document.querySelector('#status-donut .donut-num').textContent)"
INFO = "() => document.getElementById('todo-info').textContent"


def _seed(container: Any) -> None:
    repo = SqliteTaskRepository(container.db)
    now = container.clock.now()
    old = now - timedelta(days=400)
    repo.add(Task("합성 이월 업무", TaskStatus.WAITING, TaskSource.MANUAL, old))
    repo.add(
        Task(
            "합성 오래된 완료",
            TaskStatus.DONE,
            TaskSource.MANUAL,
            old,
            closed_at=old + timedelta(days=1),
        )
    )
    repo.add(Task("합성 새 업무", TaskStatus.WAITING, TaskSource.MANUAL, now))


def _click_grain(page: Any, grain: str) -> None:
    page.click(f"#seg button[data-g='{grain}']")
    page.wait_for_selector(f"#seg button[data-g='{grain}'].on")
    page.wait_for_timeout(300)  # 목록 다시 그리기(CSP로 wait_for_function 대신)


@pytest.mark.slow
def test_ui_met_period_filters_donut_and_list(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        _seed(container)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + "/")
        page.wait_for_selector("#todo tr.row")
        _click_grain(page, "all")
        everything = page.evaluate(COUNT, "")
        assert page.evaluate(DONUT) == everything
        assert page.locator("#carry-box.hidden").count() == 1  # '전체'면 이월 토글 숨김
        _click_grain(page, "month")
        since = page.evaluate(
            "() => fetch('/api/metrics?grain=month').then(r => r.json()).then(d => d.period_start)"
        )
        query = "?since=" + since.replace("+", "%2B")
        with_carry = page.evaluate(COUNT, query)
        assert page.evaluate(DONUT) == with_carry < everything  # 오래된 완료는 빠짐
        assert f"{with_carry}건" in page.evaluate(INFO) or with_carry == 0
        assert "11/" in page.inner_text("#m-period") or "까지" in page.inner_text("#m-period")
        # 이월 끄기 → 기간 이전에 생긴 미완료 업무가 빠진다
        page.uncheck("#carry")
        page.wait_for_timeout(300)
        no_carry = page.evaluate(COUNT, query + "&carry=false")
        assert page.evaluate(DONUT) == no_carry < with_carry
        # 카드 문구: 동기 대비
        assert "동기 대비" in page.inner_text("#mk")
        # 고른 기간은 다시 열어도 유지(ui_prefs)
        page.reload()
        page.wait_for_selector("#seg button[data-g='month'].on")
        assert errors == []
