"""G-Knowledge 백그라운드 등록·작업 센터 화면 테스트(N2·U1, GK-03·04·06, 결정기록 0035).

로컬 127.0.0.1 데모 서버(가짜 G-Knowledge)만 쓴다. 외부 사이트에 접속하지 않는다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from app.jobs import collect_jobs
from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container
from fixtures.ui_server import chromium, csrf_post, demo_server
from integrations.gknowledge.fake import FakeKnowledgeStore


def _done_task(page: Any, base: str, title: str) -> int:
    page.goto(base + "/")
    task = csrf_post(page, base, "/api/tasks", {"title": title})
    csrf_post(page, base, f"/api/tasks/{task['id']}/status", {"status": "done"})
    return int(task["id"])


@pytest.mark.slow
def test_ui_gk03_register_returns_immediately_and_shows_progress(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        store = container.knowledge.store
        assert isinstance(store, FakeKnowledgeStore)
        store.gate = threading.Event()  # 등록요청 직전에서 붙잡아 '등록 중'을 확인한다
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        task_id = _done_task(page, base, "합성 완료 업무 출하 재처리")
        page.goto(base + "/")
        page.locator("#filters button[data-f='done']").click()
        page.locator(f'#todo tr.row[data-id="{task_id}"]').click()
        page.locator("#d-foot button", has_text="G-Knowledge 지식 등록").click()
        page.wait_for_selector("#dlg-gk.show")
        page.click("#gk-submit")
        # 모달은 결과를 기다리지 않고 바로 닫힌다
        page.wait_for_selector("#dlg-gk.show", state="hidden", timeout=3000)
        assert "등록을 시작했습니다" in page.inner_text("#toast")
        page.wait_for_selector("#jobs-badge:not(.hidden)")
        page.click("#jobs-btn")
        page.wait_for_selector("#jobs-list .job.g-running")
        assert "등록 중" in page.inner_text("#jobs-list")
        assert "합성 완료 업무" in page.inner_text("#jobs-list")
        page.wait_for_selector("#d-gk-state .gk-st.s-running")
        store.gate.set()
        page.wait_for_selector("#d-gk-state a.link", timeout=10000)
        assert "등록 완료(검토 대기)" in page.inner_text("#d-gk-state")
        page.wait_for_selector("#jobs-list .job.g-done")
        assert page.locator("#jobs-badge.hidden").count() == 1
        assert errors == []


@pytest.mark.slow
def test_ui_gk06_unknown_result_offers_confirm_actions(tmp_path: Path) -> None:
    with demo_server(tmp_path) as (base, container), chromium() as browser:
        store = container.knowledge.store
        assert isinstance(store, FakeKnowledgeStore)
        store.register_fail = "unknown"
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        task_id = _done_task(page, base, "합성 완료 업무 결과 미확인")
        page.goto(base + "/")
        page.locator("#filters button[data-f='done']").click()
        page.locator(f'#todo tr.row[data-id="{task_id}"]').click()
        page.locator("#d-foot button", has_text="G-Knowledge 지식 등록").click()
        page.wait_for_selector("#dlg-gk.show")
        page.click("#gk-submit")
        page.wait_for_selector("#d-gk-state .gk-st.s-unknown", timeout=10000)
        page.wait_for_selector("#toast.show")
        # 자동 재시도 없음. 상세에서 '등록됨으로 표시'·'다시 시도'를 고른다
        assert store.register_calls == 1
        page.locator("#d-foot button", has_text="등록됨으로 표시").click()
        page.wait_for_selector("#d-gk-state a.link")
        assert store.register_calls == 1


def test_ui_u1_job_center_merges_sources(tmp_path: Path, clock: FixedClock) -> None:
    """작업 센터가 G-Knowledge 등록·요약·메일 수집·회사명 조회를 한 목록으로 합친다."""
    from domain.knowledge import draft_from_edit

    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})  # 요약 대기 업무가 생긴다
    task = container.tasks.add_manual("합성 업무")
    assert task.id is not None
    container.gk_queue.enqueue(task.id, draft_from_edit("합성 제목", "합성 본문", []))
    container.company_job.last_saved = 2
    data = collect_jobs(container)
    kinds = {job["kind"] for job in data["jobs"]}
    assert {"gk", "summary", "mail", "company"} <= kinds
    gk = next(job for job in data["jobs"] if job["kind"] == "gk")
    assert gk["state"] == "queued" and gk["target"] == "합성 제목" and data["active"] >= 1
