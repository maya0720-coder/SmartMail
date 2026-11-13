"""통합 시나리오(13단계, 가짜 어댑터·합성 메일).

메일 수집 → 업무 제안·승인 → 회신 감지·완료 → 히스토리 카드 → G-Knowledge 등록 → 7일 경과 삭제
→ G-Knowledge 조회 → (없으면) AI 제안 → 주간보고 초안·승인·게시 → 성과 지표 반영.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container


def test_end_to_end_with_fakes(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        # 1) 메일 수집 → 업무 제안
        collected = client.post("/api/mail/collect", json={"period": "recent7"}).json()
        assert collected["suggested"] >= 4
        suggested = client.get("/api/tasks", params={"status": "suggested"}).json()
        by_title = {t["title"]: t for t in suggested}
        fi = by_title["전표 역분개 시 오류 발생 문의"]
        po = by_title["PO 출력물 문구 변경"]

        # 2) 승인(수정 없이 / 수정 후)
        assert client.post(f"/api/tasks/{fi['id']}/approve", json={}).json()["status"] == "waiting"
        client.post(f"/api/tasks/{po['id']}/approve", json={"module_tag": "일반업무"})

        # 3) 같은 기간 재수집 → 회신 감지로 완료 후보(자동 완료 아님)
        again = client.post("/api/mail/collect", json={"period": "recent7"}).json()
        assert again["reply_detected"] >= 1
        assert client.get(f"/api/tasks/{fi['id']}").json()["status"] == "done_candidate"

        # 4) 완료 확정 → 히스토리 카드(회신 기반 초안) 저장
        assert (
            client.post(f"/api/tasks/{fi['id']}/status", json={"status": "done"}).status_code == 200
        )
        draft = client.post(f"/api/history/{fi['id']}/draft", json={}).json()
        assert draft["tcode"] == "OB52"
        card = {**draft, "tags": ["FI"]}
        assert client.post(f"/api/history/{fi['id']}", json=card).status_code == 200

        # 5) 앱 이력 검색은 앱에서 해결(외부 호출 없음)
        found = client.post("/api/search", json={"query": "역분개", "task_id": fi["id"]}).json()
        assert found["resolved"] == "app"

        # 6) G-Knowledge 등록
        preview = client.get(f"/api/gk/preview/{fi['id']}").json()
        payload = {"title": preview["title"], "body": preview["body"], "tags": preview["tags"]}
        assert client.post(f"/api/gk/register/{fi['id']}", json=payload).status_code == 200

        # 7) 주간보고 초안 → 미리보기 → 승인 후 게시
        weekly = client.post("/api/weekly/draft", json={}).json()
        assert any("역분개" in item for item in weekly["done"])
        preview_w = client.post("/api/weekly/preview", json=weekly).json()
        published = client.post(
            "/api/weekly/publish",
            json={**weekly, "nonce": preview_w["nonce"], "mode": "create", "approved": True},
        )
        assert published.status_code == 200

        # 8) 성과 지표: 진행 중 주에 완료 1건, 지식 등록 1건
        metrics = client.get("/api/metrics", params={"grain": "week"}).json()
        current = metrics["buckets"][-1]
        assert current["partial"] is True
        assert current["values"]["MET-01"]["numerator"] == 1
        assert current["values"]["MET-03"]["numerator"] == 1
        assert current["values"]["MET-04"]["numerator"] == 1

        # 9) 7일 경과 → 앱 기록 삭제(지표 원장 플래그 false면 원장도 삭제)
        clock.advance(timedelta(days=7, minutes=1))
        result = container.retention.run()
        assert result.tasks >= 1 and result.history_cards == 1
        assert container.history.search("역분개") == []

        # 10) 앱에 없으면 G-Knowledge에서 찾음, 둘 다 없으면 AI 제안
        outcome = container.search.search("역분개")
        assert outcome.resolved.value == "gknowledge"
        outcome_ai = container.search.search("야간 배치 지연")
        assert outcome_ai.resolved.value == "ai"
        assert outcome_ai.stages[2].ai_answer is not None
        assert "검증 필요" in outcome_ai.stages[2].ai_answer.label

    # 11) 메일 본문이 DB에 남지 않았는지 확인
    container.db.close()
    db_bytes = b"".join(p.read_bytes() for p in tmp_path.rglob("*.db"))
    assert "기간 마감 오류가 발생합니다".encode() not in db_bytes
