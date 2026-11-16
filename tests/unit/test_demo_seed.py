"""공모전 시연용 합성 데이터·시딩 테스트(결정기록 0021). 실제 데이터·외부 접속 없음."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from collectors.demo_data import demo_messages
from collectors.demo_scenarios import DEMO_HISTORY, DEMO_KNOWLEDGE, DOMAIN
from domain.clock import KST, FixedClock
from domain.models import TaskStatus
from fixtures.app_factory import app_client, make_container


def test_demo_mail_is_synthetic_and_covers_scenarios() -> None:
    messages = demo_messages(datetime(2026, 11, 5, tzinfo=KST))
    assert len(messages) >= 30
    assert all(m.sender_address.endswith("@" + DOMAIN) for m in messages)
    bodies = "\n".join(m.body for m in messages)
    assert "-----Original Message-----" in bodies and "보낸 사람:" in bodies  # 전달 메일
    assert any(m.sent_by_me for m in messages) and any(m.is_auto for m in messages)


def test_demo_seed_off_by_default_in_tests(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    assert container.tasks.list_tasks([TaskStatus.DONE]) == []


def test_demo_seed_enables_case_based_suggestion(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock, demo_seed=True)
    past = [c for c in DEMO_HISTORY if c.thread_id.startswith("DEMO-PAST-")]
    assert len(container.tasks.list_tasks([TaskStatus.DONE])) == len(past)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        container.summary_job.run_once(max_batches=20)
        tasks = client.get("/api/tasks").json()
        # 과거 사례와 같은 T-code를 쓰는 이번 주 업무(예: CK11N)에 대해 해결 방안을 요청한다
        target = next(t for t in tasks if t["status"] != "done" and "CK11N" in str(t))
        result = client.post(
            "/api/search", json={"query": target["title"], "task_id": target["id"], "suggest": True}
        ).json()
    stages = {s["stage"]: s for s in result["stages"]}
    assert stages["app"]["found"] and stages["ai"]["cases_used"] >= 1
    assert "참고한 사례: 1" in stages["ai"]["ai_answer"]["text"]
    assert len(DEMO_KNOWLEDGE) >= 6


def test_demo_window_is_recent(clock: FixedClock) -> None:
    base = clock.now() - timedelta(days=6)
    assert all(base <= m.received_at <= clock.now() for m in demo_messages(base))
