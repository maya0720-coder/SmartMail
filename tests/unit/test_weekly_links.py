"""주간보고 참고 링크·양식 초안·변환·게시 폴백·게시 캐시 테스트(WIKI-01~06). 가짜 위키만 쓴다."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from domain.clock import FixedClock
from domain.errors import UserInputError
from domain.weekly.convert import to_platform_text
from domain.weekly.form import (
    FormRules,
    build_layout,
    clean_heading,
    headings_from_html,
    layout_from_client,
    parse_outline,
)
from domain.weekly.links import (
    MAX_LINKS,
    Platform,
    detect_platform,
    normalize_url,
)
from domain.weekly.report import WeeklyDraft, WikiTemplate
from domain.weekly.service import WikiLoginRequiredError, WikiUnavailableError
from fixtures.app_factory import app_client, make_container
from integrations.wiki.fake import FAKE_BASE, FakeWiki
from storage.retention import RetentionJob
from storage.weekly_repos import SqliteWeeklyCache

WIKI_BASE = "https://wiki.example.invalid"


# ---- WIKI-01: 링크 검증·플랫폼 판별 ----


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://team.slack.com/archives/C123/p1", Platform.SLACK),
        ("https://slack.com/x", Platform.SLACK),
        ("https://a.enterprise.slack.com/x", Platform.SLACK),
        ("https://wiki.example.invalid/anything", Platform.WIKI),
        ("https://other.example.invalid/display/ERP/ERP_261113", Platform.WIKI),
        ("https://other.example.invalid/pages/viewpage.action?pageId=1", Platform.WIKI),
        ("https://other.example.invalid/wiki/spaces/ERP/pages/1", Platform.WIKI),
        ("https://docs.example.invalid/report", Platform.WEB),
        # 서브도메인·접미사 속임수는 슬랙이 아니다
        ("https://slack.com.evil.example/x", Platform.WEB),
        ("https://evilslack.com/x", Platform.WEB),
        ("https://slack.com.example.invalid/x", Platform.WEB),
    ],
)
def test_wiki01_detect_platform(url: str, platform: Platform) -> None:
    assert detect_platform(normalize_url(url), WIKI_BASE) is platform


@pytest.mark.parametrize(
    "url",
    [
        "http://team.slack.com/x",  # http 거부
        "slack://channel?id=1",
        "javascript:alert(1)",
        "https://user:pw@team.slack.com/x",  # 사용자 정보
        "https://team.slack.com@evil.example/x",
        "https://exa mple.com/x",
        "https://한글.example/x",  # 비ASCII
        "https://-bad-.example/x",
        "https://example.com/" + "a" * 500,  # 길이 초과
        "",
        "ftp://example.com",
        "https://example.com/a\nb",
    ],
)
def test_wiki01_rejects_bad_urls(url: str) -> None:
    with pytest.raises(UserInputError):
        normalize_url(url)


def test_wiki01_normalize_strips_fragment_and_trailing_dot() -> None:
    assert normalize_url("https://Team.Slack.com./a?b=1#frag") == "https://team.slack.com/a?b=1"


def test_wiki01_link_api_limits_and_dedup(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        first = client.post(
            "/api/weekly/links", json={"url": "https://t.slack.com/a", "name": "슬랙 양식"}
        )
        assert first.status_code == 201
        assert first.json()["platform"] == "slack" and first.json()["platform_label"] == "슬랙"
        assert (
            client.post("/api/weekly/links", json={"url": "https://t.slack.com/a"}).status_code
            == 400
        )
        assert (
            client.post("/api/weekly/links", json={"url": "http://x.example/a"}).status_code == 400
        )
        assert client.post(
            "/api/weekly/links", json={"url": "https://x.example/a", "extra": 1}
        ).status_code in (400, 422)
        for n in range(MAX_LINKS - 1):
            assert (
                client.post("/api/weekly/links", json={"url": f"https://x.example/{n}"}).status_code
                == 201
            )
        over = client.post("/api/weekly/links", json={"url": "https://x.example/over"})
        assert over.status_code == 400
        listing = client.get("/api/weekly/links").json()
        assert len(listing["links"]) == MAX_LINKS
        link_id = listing["links"][0]["id"]
        assert client.request("DELETE", f"/api/weekly/links/{link_id}", json={}).status_code == 200
        assert client.request("DELETE", f"/api/weekly/links/{link_id}", json={}).status_code == 404


def test_wiki01_link_api_requires_csrf(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        client.headers.pop("X-CSRF-Token")
        response = client.post("/api/weekly/links", json={"url": "https://t.slack.com/a"})
        assert response.status_code in (400, 403)
        assert client.get("/api/weekly/links").json()["links"] == []


# ---- WIKI-02: 양식 파싱·매핑·폴백 ----


def test_wiki02_clean_heading_and_outline() -> None:
    assert clean_heading("## 1. 금주 실적:") == "금주 실적"
    assert clean_heading("*차주 계획*") == "차주 계획"
    assert clean_heading("h3. 이슈 / 요청") == "이슈 / 요청"
    assert parse_outline("# 금주 실적\n\n- 차주 계획\n") == ["금주 실적", "차주 계획"]
    with pytest.raises(UserInputError):
        parse_outline("\n".join(f"항목{n}" for n in range(31)))


def test_wiki02_headings_from_html_ignores_script() -> None:
    body = "<h2>김가상</h2><h3>금주 완료</h3><script>alert(1)</script><h3>다음 주 예정</h3><p>x</p>"
    assert headings_from_html(body) == ["김가상", "금주 완료", "다음 주 예정"]


def test_wiki02_mapping_and_unmapped_defaults() -> None:
    defaults = ("금주 실적", "차주 계획", "이슈·요청")
    layout = build_layout(
        ["개요", "이번 주 진행 현황", "리스크 및 건의"], defaults, FormRules(), "양식"
    )
    assert layout is not None
    assert layout.headings() == ("이번 주 진행 현황", "차주 계획", "리스크 및 건의")
    assert layout.matched == 2
    # 이슈 키워드가 먼저 판정된다
    both = build_layout(["이슈 및 차주 계획"], defaults, FormRules(), "양식")
    assert both is not None and both.heading_issues == "이슈 및 차주 계획"
    assert build_layout(["개요", "참석자"], defaults, FormRules(), "양식") is None


def test_wiki02_layout_from_client_validates() -> None:
    with pytest.raises(UserInputError):
        layout_from_client("", "b", "c")
    masked = layout_from_client("실적 010-1234-5678", "계획", "이슈")
    assert "1234" not in masked.heading_done


def test_wiki02_draft_uses_pasted_outline_then_falls_back(
    tmp_path: Path, clock: FixedClock
) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        with_outline = client.post(
            "/api/weekly/links",
            json={
                "url": "https://t.slack.com/a",
                "name": "팀 양식",
                "outline": "# 완료 업무\n# 예정 업무\n# 문제점",
            },
        ).json()
        none = client.post(
            "/api/weekly/links", json={"url": "https://docs.example.invalid/x"}
        ).json()
        draft = client.post("/api/weekly/draft", json={"link_id": with_outline["id"]}).json()
        assert draft["form"]["source"] == "link"
        assert draft["form"]["headings"] == {
            "done": "완료 업무",
            "planned": "예정 업무",
            "issues": "문제점",
        }
        # 기존 필드는 그대로, 양식 정보 없음 -> 기본 양식 폴백과 사유 안내
        fallback = client.post("/api/weekly/draft", json={"link_id": none["id"]}).json()
        assert fallback["form"]["source"] == "default" and "기본 양식" in fallback["form"]["label"]
        assert fallback["form"]["headings"]["done"] == "금주 실적"
        assert {"week_start", "done", "planned", "issues"} <= set(fallback)
        assert client.post("/api/weekly/draft", json={"link_id": 999}).status_code == 400
        assert (
            "form" in client.post("/api/weekly/draft", json={}).json()
        )  # 본문 없는 기존 호출도 동작


def test_wiki02_wiki_link_parsed_via_adapter_only(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    wiki = container.weekly._wiki
    assert isinstance(wiki, FakeWiki)
    page = wiki.create_page(
        "DEMO", "ERP_261106", "<h2>김</h2><h3>금주 완료</h3><h3>차주 예정</h3><h3>이슈</h3>"
    )
    with app_client(container) as client:
        link = client.post("/api/weekly/links", json={"url": page.link}).json()
        assert link["platform"] == "wiki"
        form = client.post("/api/weekly/draft", json={"link_id": link["id"]}).json()["form"]
        assert form["source"] == "link"
        assert form["headings"] == {"done": "금주 완료", "planned": "차주 예정", "issues": "이슈"}
        # 위키 기준 주소 밖 위키 패턴 링크는 가져오지 않는다 -> 기본 양식
        outside = client.post(
            "/api/weekly/links", json={"url": "https://other.example.invalid/display/ERP/x"}
        ).json()
        assert outside["platform"] == "wiki"
        assert (
            client.post("/api/weekly/draft", json={"link_id": outside["id"]}).json()["form"][
                "source"
            ]
            == "default"
        )


def test_wiki02_fake_wiki_ignores_foreign_link() -> None:
    wiki = FakeWiki()
    wiki.create_page("S", "t", "<h2>a</h2>")
    with pytest.raises(WikiUnavailableError):
        wiki.get_page_by_link("https://evil.example/pages/1")
    assert wiki.get_page_by_link(FAKE_BASE + "1?v=1") is not None


# ---- WIKI-05: 형식 변환 ----


def _draft(clock: FixedClock) -> WeeklyDraft:
    from domain.calendar import week_start

    return WeeklyDraft(
        week_start(clock.now()), ["[ERP] <b>완료</b> & *굵게* @channel"], [], ["이슈 A"]
    )


def test_wiki05_slack_conversion_escapes(clock: FixedClock) -> None:
    text = to_platform_text(_draft(clock), WikiTemplate(), "김가상", Platform.SLACK)
    assert "*금주 실적*" in text and "• -" in text  # 빈 영역 표시
    assert "&lt;b&gt;완료&lt;/b&gt; &amp;" in text and "<b>" not in text
    assert "@channel" not in text and "@​channel" in text


def test_wiki05_wiki_markup_escapes_and_owner_toggle(clock: FixedClock) -> None:
    text = to_platform_text(_draft(clock), WikiTemplate(), "김가상", Platform.WIKI)
    assert text.startswith("h2. 김가상") and "h3. 금주 실적" in text
    assert "\\*굵게\\*" in text
    assert "김가상" not in to_platform_text(
        _draft(clock), WikiTemplate(), "김가상", Platform.WIKI, include_owner=False
    )


def test_wiki05_convert_api_masks(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        draft = client.post("/api/weekly/draft", json={}).json()
        body = {**draft, "done": ["문의 010-1234-5678"], "platform": "slack"}
        result = client.post("/api/weekly/convert", json=body)
        assert result.status_code == 200 and "1234" not in result.json()["text"]
        assert client.post("/api/weekly/convert", json={**body, "platform": "x"}).status_code in (
            400,
            422,
        )


# ---- WIKI-03·06: 승인 → 게시, 폴백, 게시 캐시 ----


def _approve_flow(
    client: TestClient,
    link_id: int | None,
    *,
    fallback: bool = True,
    bad_target: int | None = None,
) -> Any:
    draft = client.post("/api/weekly/draft", json={"link_id": link_id}).json()
    link_id = bad_target if bad_target is not None else link_id
    draft["done"] = ["[ERP] 완료 업무 (요청: 인사팀)"]
    preview = client.post("/api/weekly/preview", json=draft).json()
    body = {
        **draft,
        "nonce": preview["nonce"],
        "mode": "create",
        "approved": True,
        "fallback": fallback,
        "target_link_id": link_id,
    }
    return client.post("/api/weekly/publish", json=body)


def test_wiki06_wiki_api_publish_is_cached(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        result = _approve_flow(client, None)
        data = result.json()
        assert result.status_code == 200 and data["method"] == "api" and data["version"] == 1
        assert data["open_link"] is None
        cache = client.get("/api/weekly/cache").json()
        assert len(cache) == 1 and cache[0]["method"] == "api" and cache[0]["platform"] == "wiki"
        snapshot = client.get(f"/api/weekly/cache/{cache[0]['id']}").json()["text"]
        assert (
            "완료 업무" in snapshot and "데모 사용자" not in snapshot
        )  # 본인 이름 머리글은 저장 안 함


def test_wiki06_slack_target_falls_back_to_clipboard(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        link = client.post(
            "/api/weekly/links", json={"url": "https://t.slack.com/archives/C1"}
        ).json()
        result = _approve_flow(client, link["id"])
        data = result.json()
        assert result.status_code == 200 and data["method"] == "clipboard"
        assert (
            data["platform"] == "slack" and data["open_link"] == "https://t.slack.com/archives/C1"
        )
        assert "*금주 실적*" in data["text"] and data["cache_id"]
        # 폴백을 허용하지 않으면 자동 게시 불가로 거부, 캐시 없음
        again = _approve_flow(client, link["id"], fallback=False)
        assert again.status_code == 503
        assert len(client.get("/api/weekly/cache").json()) == 1


def test_wiki06_wiki_unavailable_falls_back(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)

    class Down(FakeWiki):
        def get_page(self, space_key: str, title: str):  # type: ignore[no-untyped-def]
            raise WikiUnavailableError("위키 API 미확인")

    container.weekly._wiki = Down()
    with app_client(container) as client:
        denied = _approve_flow(client, None, fallback=False)
        assert denied.status_code == 503  # 기존 동작 유지
        data = _approve_flow(client, None).json()
        assert data["method"] == "clipboard" and data["platform"] == "wiki"
        assert data["reason"] == "위키 API 미확인"
        assert data["open_link"] == WIKI_BASE + "/"  # 위키 기준 주소(https)
        assert "h2." in data["text"] and "<h2>" in data["html"]


def test_wiki06_login_required_is_not_a_fallback(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)

    class NeedLogin(FakeWiki):
        def get_page(self, space_key: str, title: str):  # type: ignore[no-untyped-def]
            raise WikiLoginRequiredError("위키 로그인이 필요합니다")

    container.weekly._wiki = NeedLogin()
    with app_client(container) as client:
        result = _approve_flow(client, None)
        assert result.status_code == 503 and "로그인" in result.json()["error"]
        assert client.get("/api/weekly/cache").json() == []


def test_wiki03_no_publish_without_valid_nonce(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        link = client.post("/api/weekly/links", json={"url": "https://t.slack.com/a"}).json()
        draft = client.post("/api/weekly/draft", json={}).json()
        body = {
            **draft,
            "nonce": "x" * 24,
            "mode": "create",
            "approved": True,
            "fallback": True,
            "target_link_id": link["id"],
        }
        assert client.post("/api/weekly/publish", json=body).status_code == 400
        assert (
            client.post("/api/weekly/publish", json={**body, "approved": False}).status_code == 422
        )
        assert client.get("/api/weekly/cache").json() == []


def test_wiki06_publish_unknown_target_keeps_nonce(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        assert _approve_flow(client, None, bad_target=999).status_code == 400


def test_wiki06_layout_headings_used_in_preview(tmp_path: Path, clock: FixedClock) -> None:
    with app_client(make_container(tmp_path, clock)) as client:
        link = client.post(
            "/api/weekly/links",
            json={"url": "https://t.slack.com/a", "outline": "완료 업무\n예정 업무\n문제점"},
        ).json()
        draft = client.post("/api/weekly/draft", json={"link_id": link["id"]}).json()
        preview = client.post("/api/weekly/preview", json=draft).json()
        assert "<h3>완료 업무</h3>" in preview["section_html"]
        tampered = {
            **draft,
            "form": {
                **draft["form"],
                "headings": {"done": "<img src=x>", "planned": "p", "issues": "i"},
            },
        }
        escaped = client.post("/api/weekly/preview", json=tampered).json()["section_html"]
        assert "<img" not in escaped


def test_wiki06_cache_retention_purges_old_entries(db, clock: FixedClock) -> None:  # type: ignore[no-untyped-def]
    cache = SqliteWeeklyCache(db)
    now = clock.now()
    old = cache.add(now, "slack", "clipboard", None, now - timedelta(days=8), "오래된 본문")
    fresh = cache.add(now, "wiki", "api", None, now - timedelta(days=1), "최근 본문")
    result = RetentionJob(db, clock, retention_days=7, keep_ledger=True).run()
    assert result.weekly_cache == 1
    assert cache.get(old) is None and cache.get(fresh) is not None


# ---- 보안 검토 하-4·하-5 회귀 ----


class _SpyWiki(FakeWiki):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def get_page_by_link(self, link: str):  # type: ignore[no-untyped-def]
        self.calls.append(link)
        return super().get_page_by_link(link)


def test_wiki02_foreign_origin_never_reaches_adapter(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    spy = _SpyWiki()
    container.weekly._wiki = spy
    with app_client(container) as client:
        for url in (
            "https://evil.example/display/X/page",  # 다른 호스트
            "https://wiki.example.invalid:8443/display/X",  # 다른 포트
        ):
            link = client.post(
                "/api/weekly/links", json={"url": url, "outline": "완료 업무\n예정 업무"}
            ).json()
            assert link["platform"] == "wiki"  # 배지는 경로 패턴으로 위키
            form = client.post("/api/weekly/draft", json={"link_id": link["id"]}).json()["form"]
            assert form["headings"]["done"] == "완료 업무"  # 붙여 넣은 목차로 처리
        assert spy.calls == []


def test_wiki02_confluence_adapter_rejects_foreign_origin() -> None:
    from integrations.wiki.confluence import ConfluenceWiki

    adapter = ConfluenceWiki(WIKI_BASE, None)  # type: ignore[arg-type]
    for link in ("https://evil.example/display/X", "https://wiki.example.invalid:8443/x"):
        with pytest.raises(WikiUnavailableError, match="다른 링크"):
            adapter.get_page_by_link(link)


@pytest.mark.parametrize("ch", list("!+?-#"))
def test_wiki05_wiki_markup_escapes_extra_specials(ch: str, clock: FixedClock) -> None:
    draft = WeeklyDraft(_draft(clock).week_start, [f"a{ch}b !https://x/x.png!"], [], [])
    text = to_platform_text(draft, WikiTemplate(), "김", Platform.WIKI, include_owner=False)
    assert f"a\\{ch}b" in text and "\\!https" in text


def test_wiki05_newlines_and_controls_cannot_inject_line_markup(
    tmp_path: Path, clock: FixedClock
) -> None:
    evil = "정상\nh1. 제목\r\nbq. 인용\x00\x07\n# 목록 * 불릿"
    for platform in (Platform.WIKI, Platform.SLACK, Platform.WEB):
        draft = WeeklyDraft(_draft(clock).week_start, [evil], [], [])
        text = to_platform_text(draft, WikiTemplate(), "김", platform, include_owner=False)
        assert len([ln for ln in text.splitlines() if "정상" in ln]) == 1
        assert "\x00" not in text and " " not in text
        assert not any(ln.startswith(("h1.", "bq.", "#")) for ln in text.splitlines())
    with app_client(make_container(tmp_path, clock)) as client:
        body = {**client.post("/api/weekly/draft", json={}).json(), "done": [evil]}
        out = client.post("/api/weekly/convert", json={**body, "platform": "wiki"}).json()
        assert "\nh1." not in out["text"] and "<h1>" not in out["html"]
        assert "\n정상" not in out["html"] and "\nbq." not in out["text"]
