"""주간보고 업무별 간략 요약·기본 양식 테스트(WIKI-01·02, 결정기록 0042). 합성 값만 쓴다."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from domain.clock import FixedClock
from domain.models import MailRef, MailSource, Task, TaskSource, TaskStatus
from domain.weekly.brief import EMPTY_BRIEF, TaskBrief, concise, parse_digest, task_brief
from domain.weekly.brief_render import HEADING_STATUS, brief_item, render_brief_body
from domain.weekly.report import MAX_ITEM_CHARS, WikiTemplate
from fixtures.app_factory import app_client, make_container

PHONE = "010-1234-5678"
TEMPLATE = WikiTemplate()
DIGEST = (
    "요청 사항:\n- 입고 수량 초과 오류 원인 확인 부탁드립니다.\n- |---|---|\n"
    "- (후속) 반품 입고도 같은 오류로 막혔습니다.\n기한: 금주 금요일\n필요 조치: MIGO 확인 후 회신"
)


def _task(now: datetime, **kw: object) -> Task:
    values: dict[str, object] = {
        "title": "입고 오류",
        "status": TaskStatus.IN_PROGRESS,
        "source": TaskSource.MAIL,
        "created_at": now - timedelta(days=2),
        "module_tag": "MM",
        "id": 7,
    }
    values.update(kw)
    return Task(**values)  # type: ignore[arg-type]


def _ref(at: datetime, direction: str, digest: str | None = None, ref_id: int = 1) -> MailRef:
    return MailRef(
        MailSource.OUTLOOK, f"e{ref_id}", f"m{ref_id}", "t1", at, "요약",
        task_id=7, id=ref_id, digest=digest, direction=direction,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("원인 확인 부탁드립니다.", "원인 확인 요청"),
        ("데이터를 검증했습니다. 다음 문장", "데이터를 검증함"),
        ("이관이 완료되었습니다", "이관이 완료됨"),
        ("자료를 잘 받았습니다", "자료를 잘 받음"),
        ("11/12까지 처리 가능할까요?", "11/12까지 처리 가능 문의"),
        ("- 요청 사항: 권한 부여해 주세요", "권한 부여 요청"),
        ("실패 3건이 있습니다", "실패 3건이 있음"),
        ("|---|---|", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_wiki02_concise_is_short_bullet_style(text: str | None, expected: str) -> None:
    assert concise(text) == expected


def test_wiki02_concise_masks_and_limits() -> None:
    out = concise(f"연락처 {PHONE} 로 회신 바랍니다 " + "가" * 100)
    assert PHONE not in out and len(out) <= 60 and out.endswith("…")


def test_wiki02_parse_digest_skips_noise() -> None:
    fields = parse_digest(DIGEST)
    assert fields.requests == (
        "입고 수량 초과 오류 원인 확인 부탁드립니다.",
        "(후속) 반품 입고도 같은 오류로 막혔습니다.",
    )
    assert fields.due == "금주 금요일" and fields.action == "MIGO 확인 후 회신"


def test_wiki02_brief_from_mail_summaries(clock: FixedClock) -> None:
    now = clock.now()
    refs = [
        _ref(now - timedelta(days=2), "in", DIGEST, 1),
        _ref(now - timedelta(days=1), "out", None, 2),
    ]
    brief = task_brief(_task(now, awaiting_reply_since=now - timedelta(days=1)), refs, now)
    assert brief.work == "입고 수량 초과 오류 원인 확인 요청 → 최근: 반품 입고도 같은 오류로 막힘"
    assert brief.progress.startswith("진행 중 · 회신 대기 · 내 회신 1통 · ")
    assert brief.progress.endswith("보냄")
    assert brief.eta == "금주 금요일"  # 업무 기한이 없으면 메일 요약의 기한


def test_wiki02_brief_prefers_progress_digest_and_dates(clock: FixedClock) -> None:
    now = clock.now()
    task = _task(
        now,
        status=TaskStatus.DONE,
        closed_at=now - timedelta(hours=3),
        progress_digest="결산 조정 데이터를 검증했습니다. 이후 내용은 생략",
    )
    brief = task_brief(task, [_ref(now, "in", DIGEST)], now)
    assert brief.work == "결산 조정 데이터를 검증함"
    assert brief.progress.startswith("완료") and "기한 지남" not in brief.progress
    assert brief.eta.endswith(" 완료")
    late = task_brief(_task(now, due_at=now - timedelta(days=1)), [], now)
    assert "기한 지남" in late.progress and late.work == "" and late.eta != "-"


def test_wiki02_brief_item_and_limit(clock: FixedClock) -> None:
    task = _task(clock.now(), title="가" * 290)
    assert brief_item(task, TEMPLATE, TaskBrief("검증함", "", "-")).startswith("[MM] ")
    assert len(brief_item(task, TEMPLATE, TaskBrief("나" * 60, "", "-"))) <= MAX_ITEM_CHARS
    assert brief_item(_task(clock.now()), TEMPLATE, EMPTY_BRIEF) == "[MM] 입고 오류 (요청: -)"


def test_wiki02_brief_body_follows_skill_template(clock: FixedClock) -> None:
    now = clock.now()
    evil = _task(now, title="<script>x</script>", id=9)
    done = _task(now, status=TaskStatus.DONE, id=8, closed_at=now)
    sections = {"done": [done], "planned": [_task(now), evil], "issues": []}
    briefs = {7: TaskBrief("원인 확인 요청", "진행 중 · 회신 대기", "11/20"), 8: EMPTY_BRIEF}
    body = render_brief_body(sections, briefs, TEMPLATE)
    order = ["<h3>요약</h3>", "<h3>금주 실적</h3>", f"<h3>{HEADING_STATUS}</h3>"]
    order += ["<h3>이슈·요청</h3>", "<h3>차주 계획</h3>"]
    positions = [body.index(h) for h in order]
    assert positions == sorted(positions)
    assert "완료 1건 · 진행 2건 · 이슈 0건 · 회신 대기 1건" in body
    assert "<th>진행 상황</th>" in body and "<td>원인 확인 요청</td>" in body
    assert "<script>" not in body and "&lt;script&gt;" in body
    assert "<ol>" in body and "(기한 11/20)" in body


def test_wiki02_api_draft_uses_brief_template(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        container.summary_job.run_once(50)
        for task in container.tasks.list_tasks()[:6]:
            client.post(f"/api/tasks/{task.id}/approve", json={})
        draft = client.post("/api/weekly/draft", json={}).json()
        html = draft["draft_html"]
        assert html.startswith("<h3>요약</h3>") and f"<h3>{HEADING_STATUS}</h3>" in html
        assert " → 최근: " in html or " — " in html  # 메일 요약 기반 수행 내용
        assert any(" — " in line for line in draft["planned"])  # 간단 편집 목록에도 수행 내용
        body = {"week_start": draft["week_start"], "form": draft["form"], "html": html}
        assert client.post("/api/weekly/preview", json=body).status_code == 200
