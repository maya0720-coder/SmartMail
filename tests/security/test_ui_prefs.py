"""화면 배치 설정 API 테스트(결정기록 0016, 디자인 명세 06 7장 ⑦⑧).

패널 폭 숫자만 저장한다. 허용 키·범위 밖 값과 CSRF 없는 변경을 거부한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from domain.clock import FixedClock
from fixtures.app_factory import app_client, make_container
from storage.repositories import SqliteAppSettings


def _delete(client: TestClient, url: str) -> Any:
    """화면 스크립트(api 도우미)처럼 JSON 본문을 붙여 보낸다(보안 미들웨어가 JSON 요청만 받는다)."""
    return client.request("DELETE", url, json={})


def test_ui_prefs_save_load_and_reset(tmp_path: Path, clock: FixedClock) -> None:
    """⑦ 저장한 폭이 다음 화면 렌더링에 내려오고, 초기화하면 사라진다."""
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        assert client.get("/api/ui-prefs").json() == {"detail_w": None, "triage_list_w": None}
        assert "data-detail-w" not in client.get("/").text
        saved = client.put("/api/ui-prefs", json={"detail_w": 480})
        assert saved.status_code == 200
        assert saved.json() == {"detail_w": 480, "triage_list_w": None}
        client.put("/api/ui-prefs", json={"triage_list_w": 300})
        assert 'data-detail-w="480"' in client.get("/").text
        assert 'data-tr-list-w="300"' in client.get("/suggestions").text
        # 패널 하나만 기본 폭으로
        assert _delete(client, "/api/ui-prefs/detail_w").json() == {
            "detail_w": None,
            "triage_list_w": 300,
        }
        assert _delete(client, "/api/ui-prefs/nope").status_code == 400
        # 전체 초기화
        assert _delete(client, "/api/ui-prefs").json() == {"detail_w": None, "triage_list_w": None}
        assert "data-tr-list-w" not in client.get("/suggestions").text


@pytest.mark.parametrize(
    "body",
    [
        {"detail_w": 199},  # 범위 아래
        {"detail_w": 801},  # 범위 위
        {"triage_list_w": -1},
        {"detail_w": "480"},  # 숫자만 허용
        {"detail_w": 480.5},
        {"detail_w": True},
        {"other_w": 300},  # 허용 키 2개 외 거부
        {"detail_w": 480, "note": "메모"},
    ],
)
def test_ui_prefs_rejects_bad_values(
    tmp_path: Path, clock: FixedClock, body: dict[str, object]
) -> None:
    """⑧ 범위 밖 값·숫자가 아닌 값·모르는 키는 저장하지 않는다."""
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        assert client.put("/api/ui-prefs", json=body).status_code in (400, 422)
        assert client.get("/api/ui-prefs").json() == {"detail_w": None, "triage_list_w": None}


def test_ui_prefs_change_requires_csrf(tmp_path: Path, clock: FixedClock) -> None:
    """⑧ CSRF 토큰 없는 저장·초기화는 거부한다."""
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.put("/api/ui-prefs", json={"detail_w": 400})
        client.headers.pop("X-CSRF-Token")
        assert client.put("/api/ui-prefs", json={"detail_w": 500}).status_code == 403
        assert _delete(client, "/api/ui-prefs").status_code == 403
        assert client.get("/api/ui-prefs").json()["detail_w"] == 400


def test_ui_prefs_ignores_tampered_stored_value(tmp_path: Path, clock: FixedClock) -> None:
    """DB 값이 숫자가 아니거나 범위를 벗어나면 기본 폭을 쓴다(화면에 그대로 내보내지 않음)."""
    container = make_container(tmp_path, clock)
    store = SqliteAppSettings(container.db)
    store.set("ui_detail_w", '480" onload="x')
    store.set("ui_triage_list_w", "99999")
    with app_client(container) as client:
        assert client.get("/api/ui-prefs").json() == {"detail_w": None, "triage_list_w": None}
        assert "onload" not in client.get("/").text
