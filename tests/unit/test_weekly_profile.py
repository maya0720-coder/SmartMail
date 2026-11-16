"""조직 양식 프로필 구조 추출·칸 연결·초안 생성 테스트(N4, WIKI-02, 결정기록 0037).

합성 양식 HTML만 쓴다(이름·업무 글자는 모두 가상).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from domain.clock import KST
from domain.errors import UserInputError
from domain.models import Task, TaskSource, TaskStatus
from domain.weekly.form import FormRules
from domain.weekly.profile_service import own_region
from domain.weekly.template_profile import (
    blocks_json,
    extract_blocks,
    rebind,
    render_profile,
    structure_hash,
    suggest_bindings,
    validate_bindings,
)

NOW = datetime(2026, 11, 20, 10, 0, tzinfo=KST)
OWN = "데모 사용자"

# ① 표 중심 양식(위키 페이지: 다른 팀원 영역 포함)
FORM_TABLE = (
    "<h1>ERP 주간보고</h1>"
    "<h2>김합성</h2><h3>금주 실적</h3><table><tr><th>업무명</th><th>요청</th></tr>"
    "<tr><td>남의 업무 가나다</td><td>영업팀</td></tr></table>"
    f"<h2>{OWN}</h2>"
    "<h3>금주 실적</h3><p>※ 진척률은 % 단위</p>"
    "<table><tr><th>업무명</th><th>요청 부서</th><th>일정</th><th>진척</th><th>비고</th></tr>"
    "<tr><td>나의 지난 업무 010-1234-5678</td><td>회계팀</td>"
    "<td>11/20</td><td>80%</td><td></td></tr>"
    "<tr><td>나의 업무 둘</td><td>재무팀</td><td>11/21</td><td>10%</td><td>메모</td></tr></table>"
    "<h3>차주 계획</h3><ul><li>나의 계획 셋</li></ul>"
    "<h3>이슈</h3><p>이번 주 이슈 내용 문단</p><script>alert(1)</script>"
    "<h2>박합성</h2><h3>금주 실적</h3><ul><li>다른 팀원 항목</li></ul>"
)
# ② 머리글 3단 양식(붙여넣기, 내 영역 머리글 없음)
FORM_LEVELS = (
    "<h2>금주 실적</h2><h3>개발</h3><h4>세부 진행</h4><ul><li>지난 항목</li></ul>"
    "<h2>차주 계획</h2><ol><li>계획 항목</li></ol><h2>홍길동</h2><p>남의 글</p>"
)
# ③ 고정 문구 포함 양식
FORM_FIXED = (
    "<p>※ 작성 기준: 목요일 오전</p><h3>실적</h3>"
    "<table><thead><tr><th>구분</th><th>내용</th><th>상태</th></tr></thead>"
    "<tbody><tr><td>FI</td><td>비밀 데이터 행</td><td>진행</td></tr></tbody></table>"
    "<p>참고: 금요일까지 작성</p><p>일반 문단은 버림</p>"
)


def _own(source: str) -> str:
    return own_region(source, OWN, strict=True)[0]


def test_wiki02_table_form_structure_only() -> None:
    blocks = extract_blocks(_own(FORM_TABLE))
    kinds = [(b.kind, b.text) for b in blocks]
    assert kinds == [
        ("heading", "금주 실적"),
        ("paragraph", "※ 진척률은 % 단위"),
        ("table", ""),
        ("heading", "차주 계획"),
        ("list", ""),
        ("heading", "이슈"),
    ]
    table = blocks[2]
    assert table.columns == ("업무명", "요청 부서", "일정", "진척", "비고")
    assert table.sample_rows == 2 and table.header_rows == 1
    stored = blocks_json(blocks)
    # 데이터 행 글자·다른 팀원 영역·이름·민감정보는 남지 않는다
    for leaked in (
        "나의 지난 업무",
        "회계팀",
        "010-1234",
        "나의 계획",
        "김합성",
        "박합성",
        "남의 업무",
        "alert",
        "이슈 내용",
    ):
        assert leaked not in stored


def test_wiki02_three_level_heading_form_from_paste() -> None:
    region, found = own_region(FORM_LEVELS, OWN, strict=False)
    assert not found
    rules = FormRules()
    from domain.weekly.form import classify_heading

    blocks = extract_blocks(
        region,
        keep_heading=lambda level, text: level >= 3 or classify_heading(text, rules) is not None,
    )
    assert [(b.kind, b.level, b.text, b.style) for b in blocks] == [
        ("heading", 3, "금주 실적", ""),
        ("heading", 3, "개발", ""),
        ("heading", 4, "세부 진행", ""),
        ("list", 0, "", "ul"),
        ("heading", 3, "차주 계획", ""),
        ("list", 0, "", "ol"),
    ]
    assert "홍길동" not in blocks_json(blocks)  # 이름일 수 있는 h2는 버림


def test_wiki02_fixed_text_form() -> None:
    blocks = extract_blocks(FORM_FIXED)
    assert [b.kind for b in blocks] == ["paragraph", "heading", "table", "paragraph"]
    assert (
        blocks[0].text == "※ 작성 기준: 목요일 오전" and blocks[3].text == "참고: 금요일까지 작성"
    )
    assert "비밀 데이터" not in blocks_json(blocks) and "일반 문단" not in blocks_json(blocks)


def test_wiki02_own_region_strict_and_limits() -> None:
    with pytest.raises(UserInputError):
        own_region("<h2>다른 사람</h2><h3>금주 실적</h3>", OWN, strict=True)
    with pytest.raises(UserInputError):
        extract_blocks("<p>" + "가" * 200_000 + "</p>")  # 500KB 초과
    with pytest.raises(UserInputError):
        extract_blocks("<div>구조 없음</div>")


def test_wiki02_binding_suggestion_by_keywords() -> None:
    blocks = extract_blocks(_own(FORM_TABLE))
    bindings = suggest_bindings(blocks, FormRules())
    assert bindings.headings == {0: "done", 3: "planned", 5: "issues"}
    assert bindings.columns[2] == ("title", "dept", "due", "status", "note")
    fixed = extract_blocks(FORM_FIXED)
    assert suggest_bindings(fixed, FormRules()).columns[2] == ("module", "title", "status")


def test_wiki02_bindings_validation_keeps_user_choice() -> None:
    blocks = extract_blocks(_own(FORM_TABLE))
    chosen = validate_bindings(
        blocks,
        {
            "headings": {"0": "planned"},
            "columns": {"2": ["title", "module", "due", "progress", "note"]},
        },
    )
    assert chosen.headings[0] == "planned" and chosen.headings[3] == "none"  # 빠진 칸은 비움
    assert chosen.columns[2][1] == "module"
    for bad in (
        {"headings": {"2": "done"}},  # 표 번호에 머리글 연결
        {"headings": {"0": "secret"}},
        {"columns": {"2": ["title"]}},  # 열 수 불일치
        {"columns": {"2": ["title", "x", "due", "status", "note"]}},
        {"headings": {"99": "done"}},
    ):
        with pytest.raises(UserInputError):
            validate_bindings(blocks, bad)


def _task(title: str, status: TaskStatus, **kw: object) -> Task:
    return Task(title, status, TaskSource.MAIL, NOW, id=len(title), **kw)  # type: ignore[arg-type]


def test_wiki02_draft_follows_block_and_column_order() -> None:
    blocks = extract_blocks(_own(FORM_TABLE))
    bindings = suggest_bindings(blocks, FormRules())
    done = _task(
        "합성 완료 업무", TaskStatus.DONE, requester_dept="합성팀", closed_at=NOW, module_tag="FI"
    )
    planned = _task("합성 계획 업무", TaskStatus.WAITING)
    html = render_profile(
        blocks,
        bindings,
        {"done": [done], "planned": [planned], "issues": []},
        lambda t: "항목 " + t.title,
    )
    order = [html.index(s) for s in ("금주 실적", "※ 진척률", "<table>", "차주 계획", "이슈")]
    assert order == sorted(order)
    # 표 행은 열 순서(업무명·요청 부서·일정·진척·비고)대로
    assert "<td>합성 완료 업무</td><td>합성팀</td><td>-</td><td>완료</td><td><br /></td>" in html
    assert "<li>항목 합성 계획 업무</li>" in html
    # 이슈 머리글 아래에 표·목록이 없으면 목록을 만든다(빈 항목)
    assert html.endswith("<h3>이슈</h3><ul><li><br /></li></ul>")


def test_wiki02_rebind_keeps_known_and_blanks_new_columns() -> None:
    old_blocks = extract_blocks(_own(FORM_TABLE))
    old = validate_bindings(
        old_blocks,
        {
            "headings": {"0": "issues"},
            "columns": {"2": ["title", "dept", "due", "progress", "note"]},
        },
    )
    changed = _own(FORM_TABLE).replace("<th>비고</th>", "<th>담당 변경</th><th>비고</th>")
    new_blocks = extract_blocks(changed)
    assert structure_hash(new_blocks) != structure_hash(old_blocks)
    rebound = rebind(old_blocks, old, new_blocks)
    assert rebound.headings[0] == "issues"
    assert rebound.columns[2] == ("title", "dept", "due", "progress", "note", "note")
