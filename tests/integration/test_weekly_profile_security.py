"""조직 양식 프로필 보안 검토 반영 회귀 테스트(N4·U5, WIKI-01·02, 결정기록 0037).

가짜 위키(데모)와 합성 양식 HTML만 쓴다. 이름·업무 글자는 모두 가상이다.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from domain.calendar import week_start
from domain.clock import FixedClock
from domain.errors import UserInputError
from domain.weekly.service import WikiPage
from domain.weekly.template_profile import (
    MAX_TAGS,
    bindings_from_json,
    blocks_from_json,
    blocks_json,
    extract_blocks,
)
from fixtures.app_factory import app_client, make_container
from integrations.wiki.fake import FAKE_BASE, FakeWiki

OWN = "데모 사용자"
# 내 영역 머리글이 없는 붙여넣기: 이름 머리글(h3)·'*' 문단·th 없는 표(첫 줄이 데이터)
PASTE_NO_OWN = (
    "<h3>홍가상</h3><h3>금주 실적</h3><p>* 홍가상 010-0000-0000 지난 내용</p>"
    "<table><tr><td>이가상 담당 업무</td><td>회계팀</td><td>진척</td></tr>"
    "<tr><td>데이터</td><td>합성팀</td><td>50%</td></tr></table>"
)


def _db_text(container: object) -> str:
    db = container.db  # type: ignore[attr-defined]
    parts: list[str] = []
    for table in db.table_columns():
        for row in db.query(f"SELECT * FROM {table}"):  # noqa: S608 - sqlite_master 값
            parts.extend(str(v) for v in tuple(row) if v is not None)
    return "\n".join(parts)


def test_wiki02_paste_without_own_heading_stores_no_names_or_rows(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        link = client.post("/api/weekly/links", json={"url": "https://team.slack.com/c"}).json()
        url = f"/api/weekly/links/{link['id']}/profile"
        profile = client.post(url + "/paste", json={"html": PASTE_NO_OWN}).json()["profile"]
    blocks = profile["blocks"]
    assert [(b["kind"], b["text"]) for b in blocks] == [
        ("heading", "머리글 1"),
        ("heading", "금주 실적"),
        ("table", ""),
    ]
    assert blocks[2]["columns"] == ["열1", "열2", "진척"]  # 키워드 칸만 열 이름으로
    stored = _db_text(container)
    for leaked in ("홍가상", "이가상", "회계팀", "010-0000-0000", "지난 내용"):
        assert leaked not in stored


def test_wiki02_tag_count_limit() -> None:
    with pytest.raises(UserInputError, match="태그"):
        extract_blocks("<h3>금주 실적</h3>" + "<b></b>" * (MAX_TAGS // 2 + 1))


def test_wiki02_stored_blocks_are_validated() -> None:
    good = blocks_json(extract_blocks("<h3>금주 실적</h3><ul><li>x</li></ul>"))
    assert len(blocks_from_json(good)) == 2
    base = json.loads(good)
    tampered = [
        [{**base[0], "kind": "script"}],
        [{**base[0], "level": 1}],
        [{**base[0], "text": "가" * 61}],
        [{**base[0], "kind": "paragraph", "level": 0, "text": "내용 문단"}],
        [{**base[1], "style": "div"}],
        [{**base[1], "kind": "table", "columns": ["열"] * 13, "header_rows": 1}],
        [],
        {"not": "list"},
    ]
    for value in tampered:
        with pytest.raises(UserInputError):
            blocks_from_json(json.dumps(value, ensure_ascii=False))
    with pytest.raises(UserInputError):
        blocks_from_json("{깨진")
    blocks = blocks_from_json(good)
    for raw in ("[]", "{깨진", '{"headings": {"1": "done"}}'):
        with pytest.raises(UserInputError):
            bindings_from_json(blocks, raw)


def test_wiki02_tampered_store_is_not_used(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        link = client.post("/api/weekly/links", json={"url": "https://team.slack.com/d"}).json()
        url = f"/api/weekly/links/{link['id']}/profile"
        client.post(url + "/paste", json={"html": "<h3>금주 실적</h3><ul><li>x</li></ul>"})
        sql = "UPDATE weekly_template_profile SET bindings_json = ? WHERE link_id = ?"
        with container.db.transaction() as conn:
            conn.execute(sql, ("[]", link["id"]))
        # 칸 연결이 어긋나면 다시 제안한다
        assert client.get(url).json()["profile"]["bindings"]["headings"] == {"0": "done"}
        bad = json.dumps(
            [
                {
                    "kind": "script",
                    "level": 0,
                    "text": "",
                    "columns": [],
                    "header_rows": 0,
                    "sample_rows": 0,
                    "style": "",
                }
            ]
        )
        sql = "UPDATE weekly_template_profile SET blocks_json = ? WHERE link_id = ?"
        with container.db.transaction() as conn:
            conn.execute(sql, (bad, link["id"]))
        # 구조가 범위를 벗어나면 쓰지 않는다(양식 없음 → 다시 등록 안내)
        assert client.get(url).json()["profile"] is None
        draft = client.post("/api/weekly/draft", json={"link_id": link["id"]})
        assert draft.status_code == 200 and draft.json()["profile"] is None


def _wiki(container: object) -> FakeWiki:
    wiki = container.weekly._wiki  # type: ignore[attr-defined]  # noqa: SLF001
    assert isinstance(wiki, FakeWiki)
    return wiki


def test_wiki02_check_handles_only_expected_failures(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    wiki = _wiki(container)
    page = f"<h2>{OWN}</h2><h3>금주 실적</h3><ul><li>x</li></ul>"
    wiki.pages["양식"] = WikiPage("7", "양식", 1, page, FAKE_BASE + "7")
    with app_client(container) as client:
        link = client.post("/api/weekly/links", json={"url": FAKE_BASE + "7"}).json()
        assert client.post(f"/api/weekly/links/{link['id']}/profile/import", json={}).is_success
    service = container.weekly_profiles
    wiki.read_fail = "error"
    _profile, changed, checked = service.check(link["id"])
    assert (changed, checked) == (False, False) and service.status.reason == "failed"
    wiki.read_fail = None

    def broken(_link: str) -> str:
        raise RuntimeError("예상 밖 오류")

    wiki.read_page_html = broken  # type: ignore[method-assign,assignment]
    with pytest.raises(RuntimeError):
        service.check(link["id"])  # 예상 밖 오류는 삼키지 않는다


def test_wiki01_carryover_duplicate_own_heading_falls_back(
    tmp_path: Path, clock: FixedClock
) -> None:
    container = make_container(tmp_path, clock)
    last = week_start(week_start(clock.now()) - timedelta(days=1))
    title = container.weekly.template.page_title(last)
    body = f"<h2>{OWN}</h2><h3>차주 계획</h3><ul><li>가</li></ul><h2>{OWN}</h2>"
    _wiki(container).pages[title] = WikiPage("9", title, 1, body, FAKE_BASE + "9")
    with app_client(container) as client:
        result = client.post("/api/weekly/carryover", json={})
    assert result.status_code == 200 and result.json() == {"source": None, "items": []}


def test_wiki02_draft_body_rejects_profile_object(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        draft = client.post("/api/weekly/draft", json={}).json()
        assert draft["profile"] is None
        assert client.post("/api/weekly/preview", json=draft).status_code == 200
        forged = {**draft, "profile": {"blocks": [{"kind": "script"}]}}
        assert client.post("/api/weekly/preview", json=forged).status_code == 422
