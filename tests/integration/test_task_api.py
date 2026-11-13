"""메일 수집 → 업무 제안 → 승인·상태 변경 API 흐름(데모 모드, 합성 메일)."""

from __future__ import annotations

from pathlib import Path

from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container


def test_collect_suggest_approve_flow(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        collected = client.post("/api/mail/collect", json={"period": "recent7"}).json()
        assert collected["suggested"] >= 3
        assert collected["excluded_messages"] >= 2
        suggested = client.get("/api/tasks", params={"status": "suggested"}).json()
        first = suggested[0]
        detail = client.get(f"/api/tasks/{first['id']}").json()
        assert detail["evidence"]
        assert detail["mail_refs"]
        ref_id = detail["mail_refs"][0]["id"]
        mail = client.get(f"/api/mail/{ref_id}").json()
        assert mail["missing"] is False and mail["body"]

        approved = client.post(
            f"/api/tasks/{first['id']}/approve", json={"module_tag": "전자결재"}
        ).json()
        assert approved["status"] == "waiting"
        done = client.post(f"/api/tasks/{first['id']}/status", json={"status": "done"}).json()
        assert done["status"] == "done" and done["retention_days_left"] == 7

        bad = client.post(f"/api/tasks/{first['id']}/status", json={"status": "suggested"})
        assert bad.status_code == 409
        # 설정 목록에 없는 구분은 서비스가 거부한다(결정기록 0014)
        invalid = client.post(f"/api/tasks/{first['id']}/edit", json={"module_tag": "<x>"})
        assert invalid.status_code == 400 and "구분" in invalid.json()["error"]
        mass = client.post(f"/api/tasks/{first['id']}/edit", json={"status": "done"})
        assert mass.status_code == 422  # 대량 할당 차단

        manual = client.post("/api/tasks", json={"title": "수동 업무", "due_date": "2026-11-20"})
        assert manual.status_code == 200 and manual.json()["source"] == "manual"
        assert client.get("/api/tasks/99999").status_code == 404

        # 같은 기간을 다시 수집해도 업무가 중복 생성되지 않는다(TODO-04)
        again = client.post("/api/mail/collect", json={"period": "recent7"}).json()
        assert again["suggested"] == 0
