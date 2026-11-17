"""주간보고 HTML 정리·복사 형식·형식별 게시 API 테스트(C1·C2, WIKI-02·03·05·06, 결정기록 0041).

가짜 위키(데모)만 쓴다. 실제 위키·Slack·Teams·메일에 접속하지 않으며 메일을 보내지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from domain.clock import FixedClock
from domain.weekly.rich import MAX_HTML_BYTES
from fixtures.app_factory import app_client, make_container

PHONE = "010-1234-5678"
HTML = (
    "<h3>금주 실적</h3><ul><li>항목 @here</li></ul>"
    f"<table><tr><th>구분</th></tr><tr><td>연락 {PHONE}</td></tr></table>"
    '<p onclick="x()" style="color: red">빨강</p><script>alert(1)</script>'
)


def _body(client: TestClient) -> dict[str, Any]:
    draft = client.post("/api/weekly/draft", json={}).json()
    return {"week_start": draft["week_start"], "form": draft["form"], "html": HTML}


def _preview(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    result = client.post("/api/weekly/preview", json=body)
    assert result.status_code == 200
    return dict(result.json())


def test_wiki02_clean_html_returns_masked_and_summary(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        result = client.post("/api/weekly/clean-html", json={"html": HTML})
        assert result.status_code == 200
        data = result.json()
        assert PHONE not in data["html"] and "<script" not in data["html"]
        assert '<p style="color: red">빨강</p>' in data["html"]
        assert data["removed"] == {"onclick 속성": 1, "script": 1} and data["blocked"] is None
        split = client.post("/api/weekly/clean-html", json={"html": "<p>010-1234-<b>5678</b></p>"})
        assert split.json()["blocked"]  # 서식 사이 민감정보는 경고
        too_big = {"html": "a" * (MAX_HTML_BYTES + 1)}
        assert client.post("/api/weekly/clean-html", json=too_big).status_code == 422
        assert client.post("/api/weekly/clean-html", json={"html": "x", "y": 1}).status_code == 422
    # 정리 API는 저장하지 않는다
    text = "\n".join(
        str(v)
        for table in container.db.table_columns()
        for row in container.db.query(f"SELECT * FROM {table}")  # noqa: S608 - sqlite_master 값
        for v in tuple(row)
        if v is not None
    )
    assert "빨강" not in text


def test_wiki05_formats_list(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        formats = client.get("/api/weekly/formats").json()
        assert [f["key"] for f in formats][:3] == ["wiki", "wiki_source", "slack"]
        assert [f["key"] for f in formats if f["direct"]] == ["wiki"]
        mail = next(f for f in formats if f["key"] == "mail")
        assert mail["platform"] == "mail" and mail["has_html"] is True


@pytest.mark.parametrize(
    "fmt", ["wiki", "wiki_source", "slack", "teams", "mail", "markdown", "text"]
)
def test_wiki05_convert_with_format_is_masked(tmp_path: Path, clock: FixedClock, fmt: str) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        body = _body(client)
        result = client.post("/api/weekly/convert", json={**body, "format": fmt})
        assert result.status_code == 200
        data = result.json()
        assert data["format"] == fmt
        assert PHONE not in data["text"] and PHONE not in (data["html"] or "")
        assert "<script" not in (data["html"] or "") and "onclick" not in (data["html"] or "")
        if fmt == "slack":
            assert "@here" not in data["text"]


def test_wiki05_convert_rejects_unknown_format(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        body = _body(client)
        assert client.post("/api/weekly/convert", json={**body, "format": "fax"}).status_code == 422


def test_wiki06_publish_mail_format_is_copy_only(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        body = _body(client)
        preview = _preview(client, body)
        approved = {
            **body,
            "nonce": preview["nonce"],
            "mode": "create",
            "approved": True,
            "fallback": True,
        }
        result = client.post("/api/weekly/publish", json={**approved, "format": "mail"})
        assert result.status_code == 200
        data = result.json()
        assert data["method"] == "clipboard" and data["platform"] == "mail"
        assert data["mailto"].startswith("mailto:?subject=")
        assert "&" not in data["mailto"] and "body=" not in data["mailto"]  # 본문·받는 사람 없음
        assert data["html"].startswith("<div style=") and PHONE not in data["text"] + data["html"]
        cache = client.get("/api/weekly/cache").json()
        assert len(cache) == 1 and cache[0]["platform"] == "mail" and PHONE not in str(cache)
        # 같은 nonce로 두 번 게시하지 못한다
        assert (
            client.post("/api/weekly/publish", json={**approved, "format": "mail"}).status_code
            == 400
        )
    # 위키 페이지는 만들지 않았다(직접 게시는 위키 형식만)
    assert client is not None and container.publish_log.list_recent() == []


def test_wiki03_publish_wiki_format_posts_directly(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        body = _body(client)
        preview = _preview(client, body)
        result = client.post(
            "/api/weekly/publish",
            json={
                **body,
                "nonce": preview["nonce"],
                "mode": "create",
                "approved": True,
                "format": "wiki",
            },
        )
        assert result.status_code == 200
        data = result.json()
        assert data["method"] == "api" and data["platform"] == "wiki" and data["mailto"] is None
    assert len(container.publish_log.list_recent()) == 1


def test_wiki03_publish_without_format_keeps_old_behavior(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        body = _body(client)
        preview = _preview(client, body)
        result = client.post(
            "/api/weekly/publish",
            json={**body, "nonce": preview["nonce"], "mode": "create", "approved": True},
        )
        assert result.status_code == 200 and result.json()["method"] == "api"
        assert result.json()["format"] is None


def test_wiki03_publish_requires_preview_and_approval(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        body = _body(client)
        no_nonce = {**body, "nonce": "없는-번호-0000000000", "mode": "create", "approved": True}
        assert (
            client.post("/api/weekly/publish", json={**no_nonce, "format": "teams"}).status_code
            == 400
        )
        preview = _preview(client, body)
        not_approved = {**body, "nonce": preview["nonce"], "mode": "create", "approved": False}
        assert client.post(
            "/api/weekly/publish", json={**not_approved, "format": "teams"}
        ).status_code in (
            400,
            422,
        )


def test_wiki02_preview_reports_removed(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        preview = _preview(client, _body(client))
        assert preview["removed"] == {"onclick 속성": 1, "script": 1}
        assert PHONE not in preview["section_html"]


def test_wiki01_link_teams_platform_saved(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        created = client.post(
            "/api/weekly/links", json={"url": "https://teams.microsoft.com/l/channel/x"}
        )
        assert created.status_code == 201 and created.json()["platform"] == "teams"
