"""회신 대기·팔로업 화면 테스트(U7, TODO-05·07, 결정기록 0038).

로컬 127.0.0.1 데모 서버만 쓴다. 메일을 보내지 않는다(초안 복사만).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from fixtures.ui_server import chromium, csrf_post, demo_server
from storage.repositories import SqliteTaskRepository


@pytest.mark.slow
def test_ui_todo07_followup_filter_badge_copy_and_clear(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + "/")
        created = csrf_post(page, base, "/api/tasks", {"title": "합성 회신 대기 업무"})
        repo = SqliteTaskRepository(container.db)
        task = repo.get(int(created["id"]))
        assert task is not None
        # 6일 전에 보낸 질문(근무일 2일 이상 경과)
        repo.update(replace(task, awaiting_reply_since=container.clock.now() - timedelta(days=6)))
        page.goto(base + "/")
        follow = page.locator("#filters button[data-f='followup']")
        follow.wait_for()
        assert follow.inner_text().endswith(" 1")
        follow.click()
        row = page.locator(f'#todo tr.row[data-id="{task.id}"]')
        assert "팔로업 필요" in row.inner_text()
        row.click()
        page.locator("#d-foot button", has_text="팔로업 메일 초안 복사").click()
        page.wait_for_selector("#toast.show")
        assert "초안을 복사했습니다" in page.inner_text("#toast")
        copied = page.evaluate("() => navigator.clipboard.readText()")
        assert "합성 회신 대기 업무" in copied and "@" not in copied
        page.locator("#d-foot button", has_text="대기 해제").click()
        page.wait_for_selector("#d-foot button:has-text('회신 대기로 표시')")
        assert page.locator("#filters button[data-f='followup']").inner_text().endswith(" 0")
        assert errors == []
