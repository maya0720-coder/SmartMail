"""조직 양식 프로필 API 테스트(N4, WIKI-02, 결정기록 0037).

가짜 위키(데모)만 쓴다. 실제 위키에 접속하지 않는다. 양식 HTML은 합성 데이터다.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.container import Container
from app.jobs import collect_jobs
from domain.clock import FixedClock
from domain.models import TaskStatus
from domain.weekly.service import WikiPage
from fixtures.app_factory import app_client, make_container
from integrations.wiki.fake import FAKE_BASE, FakeWiki
from storage.db import Database

OWN = "데모 사용자"
PAGE = (
    "<h2>김합성</h2><h3>금주 실적</h3><ul><li>남의 항목 가나다</li></ul>"
    f"<h2>{OWN}</h2><h3>금주 실적</h3>"
    "<table><tr><th>업무명</th><th>요청 부서</th><th>진척</th></tr>"
    "<tr><td>지난 데이터 행 라마바</td><td>회계팀</td><td>80%</td></tr></table>"
    "<h3>차주 계획</h3><ul><li>지난 계획 사아자</li></ul>"
)


def _wiki(container: Container) -> FakeWiki:
    wiki = container.weekly._wiki  # noqa: SLF001 - 데모 가짜 위키
    assert isinstance(wiki, FakeWiki)
    return wiki


def _db_text(db: Database) -> str:
    parts: list[str] = []
    for table in db.table_columns():
        for row in db.query(f"SELECT * FROM {table}"):  # noqa: S608 - sqlite_master 값
            parts.extend(str(v) for v in tuple(row) if v is not None)
    return "\n".join(parts)


def _wiki_link(client: TestClient, container: Container, body: str = PAGE) -> int:
    _wiki(container).pages["ERP_양식"] = WikiPage("7", "ERP_양식", 1, body, FAKE_BASE + "7")
    created = client.post("/api/weekly/links", json={"url": FAKE_BASE + "7", "name": "합성팀 양식"})
    assert created.status_code == 201 and created.json()["platform"] == "wiki"
    return int(created.json()["id"])


def test_wiki02_import_from_wiki_stores_structure_only(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        link_id = _wiki_link(client, container)
        result = client.post(f"/api/weekly/links/{link_id}/profile/import", json={})
        assert result.status_code == 200
        profile = result.json()["profile"]
        assert [b["kind"] for b in profile["blocks"]] == ["heading", "table", "heading", "list"]
        assert profile["bindings"]["columns"]["1"] == ["title", "dept", "status"]
        links = client.get("/api/weekly/links").json()["links"]
        assert links[0]["has_profile"] is True
        jobs = collect_jobs(container)["jobs"]
        assert any(j["kind"] == "wiki" and j["state"] == "done" for j in jobs)
    text = _db_text(container.db)
    for leaked in ("김합성", "남의 항목", "지난 데이터 행", "회계팀", "지난 계획"):
        assert leaked not in text


def test_wiki02_import_errors(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    wiki = _wiki(container)
    with app_client(container) as client:
        link_id = _wiki_link(client, container)
        wiki.read_fail = "login"
        login = client.post(f"/api/weekly/links/{link_id}/profile/import", json={})
        assert login.status_code == 503 and login.json()["code"] == "login_required"
        wiki.read_fail = None
        # 내 영역 머리글이 없으면 거부(다른 팀원 영역 보호)
        wiki.pages["ERP_양식"] = WikiPage(
            "7", "ERP_양식", 1, "<h2>남</h2><h3>실적</h3>", FAKE_BASE + "7"
        )
        assert (
            client.post(f"/api/weekly/links/{link_id}/profile/import", json={}).status_code == 400
        )
        # 500KB 초과 본문은 읽지 않는다
        big = f"<h2>{OWN}</h2><p>" + "가" * 200_000 + "</p>"
        wiki.pages["ERP_양식"] = WikiPage("7", "ERP_양식", 1, big, FAKE_BASE + "7")
        assert (
            client.post(f"/api/weekly/links/{link_id}/profile/import", json={}).status_code == 503
        )
        # 위키 기준 주소가 아닌 링크는 읽지 않는다(경로만 위키 모양)
        other = client.post(
            "/api/weekly/links", json={"url": "https://other.example.invalid/display/SP/x"}
        ).json()
        assert other["platform"] == "wiki"
        assert (
            client.post(f"/api/weekly/links/{other['id']}/profile/import", json={}).status_code
            == 503
        )
        # 슬랙 링크는 위키 읽기 대상이 아님
        slack = client.post("/api/weekly/links", json={"url": "https://team.slack.com/a"}).json()
        assert (
            client.post(f"/api/weekly/links/{slack['id']}/profile/import", json={}).status_code
            == 400
        )
        assert client.post("/api/weekly/links/999/profile/import", json={}).status_code == 404


def test_wiki02_paste_bindings_and_validation(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        link = client.post("/api/weekly/links", json={"url": "https://team.slack.com/b"}).json()
        url = f"/api/weekly/links/{link['id']}/profile"
        pasted = client.post(url + "/paste", json={"html": PAGE})
        assert pasted.status_code == 200
        # 붙여넣기 상한(8만 자) 초과는 입력 검증(422), 요청 본문 상한 초과는 413
        assert client.post(url + "/paste", json={"html": "x" * 80_001}).status_code == 422
        assert client.post(url + "/paste", json={"html": "x" * 500_001}).status_code == 413
        saved = client.put(
            url + "/bindings",
            json={"headings": {"0": "planned"}, "columns": {"1": ["title", "module", "due"]}},
        )
        assert saved.status_code == 200
        again = client.get(url).json()["profile"]["bindings"]
        assert again["headings"]["0"] == "planned" and again["columns"]["1"] == [
            "title",
            "module",
            "due",
        ]
        assert client.put(url + "/bindings", json={"columns": {"1": ["title"]}}).status_code == 400
        assert client.put(url + "/bindings", json={"headings": {"0": "secret"}}).status_code == 422
        assert client.put(url + "/bindings", json={"extra": 1}).status_code == 422
        assert client.request("DELETE", url, json={}).status_code == 200
        assert client.get(url).json()["profile"] is None
        assert client.request("DELETE", url, json={}).status_code == 404


def test_wiki02_draft_uses_profile_order(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    task = container.tasks.add_manual("합성 완료 업무 하나")
    assert task.id is not None
    container.tasks.change_status(task.id, TaskStatus.DONE)
    with app_client(container) as client:
        link_id = _wiki_link(client, container)
        client.post(f"/api/weekly/links/{link_id}/profile/import", json={})
        draft = client.post("/api/weekly/draft", json={"link_id": link_id}).json()
        html = draft["draft_html"]
        assert draft["profile"]["link_id"] == link_id
        assert html.index("<h3>금주 실적</h3>") < html.index("<table>") < html.index("차주 계획")
        assert "<th>업무명</th><th>요청 부서</th><th>진척</th>" in html
        assert "<td>합성 완료 업무 하나</td>" in html
        # 프로필이 없는 링크·기본 양식은 profile 없음(화면이 '기본 양식 사용 중' 안내)
        plain = client.post("/api/weekly/draft", json={}).json()
        assert plain["profile"] is None


def test_wiki02_check_only_with_session(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    wiki = _wiki(container)
    with app_client(container) as client:
        link_id = _wiki_link(client, container)
        client.post(f"/api/weekly/links/{link_id}/profile/import", json={})
        url = f"/api/weekly/links/{link_id}/profile/check"
        wiki.logged_in = False
        calls = wiki.read_calls
        none = client.post(url, json={}).json()
        assert none["checked"] is False and none["changed"] is False and wiki.read_calls == calls
        wiki.logged_in = True
        same = client.post(url, json={}).json()
        assert same["checked"] is True and same["changed"] is False
        changed_body = PAGE.replace("<th>진척</th>", "<th>진척</th><th>새 열</th>")
        wiki.pages["ERP_양식"] = WikiPage("7", "ERP_양식", 2, changed_body, FAKE_BASE + "7")
        changed = client.post(url, json={}).json()
        assert changed["changed"] is True
        assert changed["profile"]["bindings"]["columns"]["1"] == ["title", "dept", "status", "note"]


def _last_week_cache(container: Container, clock: FixedClock, snapshot: str) -> None:
    from domain.calendar import week_start

    last = week_start(week_start(clock.now()) - timedelta(days=1))
    container.weekly_cache.add(last, "slack", "clipboard", None, clock.now(), snapshot)


def test_wiki01_carryover_classifies_last_week_plan(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    _wiki(container).logged_in = False  # 위키 세션 없음 → 게시 캐시 사용
    done = container.tasks.add_manual("합성 완료 업무")
    going = container.tasks.add_manual("합성 진행 업무")
    assert done.id is not None and going.id is not None
    container.tasks.change_status(done.id, TaskStatus.DONE)
    container.tasks.change_status(going.id, TaskStatus.IN_PROGRESS)
    _last_week_cache(
        container,
        clock,
        "*금주 실적*\n• 지난 실적\n\n*차주 계획*\n• [일반업무] 합성 완료 업무 (요청: -)\n"
        "• [일반업무] 합성 진행 업무 (요청: -)\n• 새로 세운 합성 계획\n",
    )
    before = _db_text(container.db)
    with app_client(container) as client:
        result: dict[str, Any] = client.post("/api/weekly/carryover", json={}).json()
    assert result["source"] == "cache"
    kinds = {item["text"]: item["kind"] for item in result["items"]}
    assert kinds == {
        "[일반업무] 합성 완료 업무 (요청: -)": "done",
        "[일반업무] 합성 진행 업무 (요청: -)": "progress",
        "새로 세운 합성 계획": "keep",
    }
    assert _db_text(container.db) == before  # 비교 결과를 저장하지 않는다


def test_wiki01_carryover_without_history(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        assert client.post("/api/weekly/carryover", json={}).json() == {"source": None, "items": []}


def test_wiki01_carryover_reads_own_wiki_section(tmp_path: Path, clock: FixedClock) -> None:
    """위키 세션이 있으면 지난주 페이지의 내 영역만 읽는다(다른 팀원 계획은 섞이지 않음)."""
    from domain.calendar import week_start

    container = make_container(tmp_path, clock)
    last = week_start(week_start(clock.now()) - timedelta(days=1))
    title = container.weekly.template.page_title(last)
    body = (
        "<h2>김합성</h2><h3>차주 계획</h3><ul><li>남의 합성 계획</li></ul>"
        f"<h2>{OWN}</h2><h3>차주 계획</h3><ul><li>나의 합성 계획</li></ul>"
    )
    _wiki(container).pages[title] = WikiPage("9", title, 1, body, FAKE_BASE + "9")
    with app_client(container) as client:
        result = client.post("/api/weekly/carryover", json={}).json()
    assert result["source"] == "wiki"
    assert [i["text"] for i in result["items"]] == ["나의 합성 계획"]
