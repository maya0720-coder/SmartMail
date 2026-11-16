"""G-Knowledge 2단계 등록 화면 자동화(GK-03·04·06, 결정기록 0010·0032).

실제 사이트에 접속하지 않는다.
"""

from __future__ import annotations

from typing import Any

import pytest

from domain.clock import FixedClock
from domain.knowledge import KnowledgeUnavailableError, draft_from_edit
from fixtures.gk_pages import FakePage, browser_store
from integrations.gknowledge.browser import RegisterFields, load_fields

RESULT = "https://gk.example.invalid/knowledge/77"
TWO_STEP = RegisterFields(
    "/knowledge/new",
    "#title",
    ".ql-editor",
    "",
    "#confirm-btn",
    "",
    "/knowledge/[0-9]+",
    analyze="#analyze-btn",
    analysis_ready="#c-title",
)


def _actions(log: list[Any]) -> list[tuple[str, str]]:
    return [(e[0], e[1]) for e in log if e[0] in ("fill", "click")]


def test_gk03_default_map_is_ready_with_result_message() -> None:
    register, _search = load_fields()
    assert register.analyze == "#analyze-btn" and register.submit == "#confirm-btn"
    # 실제 화면은 등록요청 뒤 주소가 그대로라 접수 안내 글자로 확인한다(결정기록 0032)
    assert register.result_message == "text=등록 요청이 접수되었습니다"
    assert register.missing() == [] and register.ready


def test_gk03_missing_result_check_is_named() -> None:
    fields = RegisterFields("/n", "#t", "#b", "", "#s", "", "")
    assert fields.missing() == ["result_message(또는 result_path·result_link)"]


def test_gk03_not_ready_message_names_missing_settings(clock: FixedClock) -> None:
    empty = RegisterFields("/knowledge/new", "#title", "", "", "", "", "")
    store, log, runtime = browser_store(clock, RESULT, fields=empty)
    try:
        with pytest.raises(KnowledgeUnavailableError) as info:
            store.register(draft_from_edit("제목", "본문", []))
        assert "확인 필요" in str(info.value)
        assert "body" in str(info.value) and "result_message" in str(info.value)
        assert not log  # 브라우저를 열지 않는다
    finally:
        runtime.shutdown()


def test_gk03_analyze_needs_ready_marker() -> None:
    fields = RegisterFields("/n", "#t", "#b", "", "#s", "", "/k/[0-9]+", analyze="#a")
    assert fields.missing() == ["analysis_ready"]


def test_gk03_two_step_fill_analyze_then_confirm(clock: FixedClock) -> None:
    store, log, runtime = browser_store(clock, RESULT, fields=TWO_STEP)
    try:
        result = store.register(draft_from_edit("제목", "본문 첫 줄\n둘째 줄", ["FI"]))
        assert result.link == RESULT
        assert [(e[0], e[1]) for e in log if e[0] in ("fill", "click", "wait_filled")] == [
            ("fill", "#title"),
            ("fill", ".ql-editor"),
            ("click", "#analyze-btn"),
            ("wait_filled", "#c-title"),  # 결과 제목에 글자가 들어온 뒤에만 등록요청
            ("click", "#confirm-btn"),
        ]
        # AI 분석 결과 칸(#c-*)은 건드리지 않는다(사용자 결정)
        assert not [e for e in log if e[0] == "fill" and str(e[1]).startswith("#c-")]
    finally:
        runtime.shutdown()


class SlowAnalysisPage(FakePage):
    def wait_for_selector(self, selector: str, timeout: int) -> None:
        if selector == "#c-title":
            raise TimeoutError("분석 결과 없음")
        super().wait_for_selector(selector, timeout)


def test_gk06_analysis_timeout_is_not_sent(clock: FixedClock) -> None:
    store, log, runtime = browser_store(clock, RESULT, page_cls=SlowAnalysisPage, fields=TWO_STEP)
    try:
        with pytest.raises(KnowledgeUnavailableError, match="AI 분석이 끝나지 않아 등록하지"):
            store.register(draft_from_edit("제목", "본문", []))
        assert ("click", "#confirm-btn") not in _actions(log)  # 등록요청 전에 멈춤
    finally:
        runtime.shutdown()


class LostBodyPage(FakePage):
    def inner_text(self, selector: str) -> str:
        return ""  # 편집기에 본문이 들어가지 않은 경우


def test_gk06_body_not_entered_is_not_sent(clock: FixedClock) -> None:
    store, log, runtime = browser_store(clock, RESULT, page_cls=LostBodyPage, fields=TWO_STEP)
    try:
        with pytest.raises(KnowledgeUnavailableError, match="본문 입력을 확인하지 못해"):
            store.register(draft_from_edit("제목", "본문", []))
        assert not [a for a in _actions(log) if a[0] == "click"]
    finally:
        runtime.shutdown()


class QuillPage(FakePage):
    def inner_text(self, selector: str) -> str:
        # Quill은 줄마다 문단으로 나눠 줄바꿈 모양이 달라질 수 있다
        return self.filled.get(selector, "").replace("\n", "\n\n") + "\n"


def test_gk03_body_check_ignores_editor_whitespace(clock: FixedClock) -> None:
    store, _log, runtime = browser_store(clock, RESULT, page_cls=QuillPage, fields=TWO_STEP)
    try:
        assert store.register(draft_from_edit("제목", "첫 줄\n둘째 줄", [])).link == RESULT
    finally:
        runtime.shutdown()


class EmptyTitlePage(FakePage):
    def wait_for_function(self, expression: str, arg: Any = None, timeout: int = 0) -> None:
        raise TimeoutError("결과 제목이 비어 있음")  # 칸은 보이지만 값이 채워지지 않은 경우


def test_gk06_empty_analysis_title_is_not_sent(clock: FixedClock) -> None:
    store, log, runtime = browser_store(clock, RESULT, page_cls=EmptyTitlePage, fields=TWO_STEP)
    try:
        with pytest.raises(KnowledgeUnavailableError, match="AI 분석이 끝나지 않아 등록하지"):
            store.register(draft_from_edit("제목", "본문", []))
        assert ("click", "#confirm-btn") not in _actions(log)
    finally:
        runtime.shutdown()


def test_gk03_analysis_wait_uses_only_remaining_time(clock: FixedClock) -> None:
    ticks = iter([0.0, 0.0, 0.0, 61.0])  # 시작·마감 확인·분석 시작·칸 표시 후(60초 초과)
    store, log, runtime = browser_store(
        clock, RESULT, now=lambda: next(ticks, 61.0), fields=TWO_STEP
    )
    try:
        with pytest.raises(KnowledgeUnavailableError, match="AI 분석이 끝나지 않아"):
            store.register(draft_from_edit("제목", "본문", []))
        assert ("click", "#confirm-btn") not in _actions(log)
    finally:
        runtime.shutdown()


MESSAGE_STEP = RegisterFields(
    "/knowledge/new",
    "#title",
    ".ql-editor",
    "",
    "#confirm-btn",
    "",
    "",
    analyze="#analyze-btn",
    analysis_ready="#c-title",
    result_message="text=등록 요청이 접수되었습니다",
)


def test_gk03_result_message_confirms_on_same_url(clock: FixedClock) -> None:
    # 등록요청 뒤 주소가 등록 화면 그대로여도 접수 안내가 보이면 성공(링크는 G-Knowledge 첫 화면)
    same = "https://gk.example.invalid/knowledge/new"
    store, _log, runtime = browser_store(clock, same, fields=MESSAGE_STEP)
    try:
        assert store.register(draft_from_edit("제목", "본문", [])).link == (
            "https://gk.example.invalid/"
        )
    finally:
        runtime.shutdown()


class NoReceiptPage(FakePage):
    def wait_for_selector(self, selector: str, timeout: int) -> None:
        if selector.startswith("text="):
            raise TimeoutError("접수 안내 없음")


def test_gk06_missing_receipt_is_uncertain_not_retried(clock: FixedClock) -> None:
    store, log, runtime = browser_store(clock, RESULT, page_cls=NoReceiptPage, fields=MESSAGE_STEP)
    try:
        with pytest.raises(KnowledgeUnavailableError, match="등록 결과를 확인하지 못했습니다"):
            store.register(draft_from_edit("제목", "본문", []))
        assert _actions(log).count(("click", "#confirm-btn")) == 1  # 다시 보내지 않음
    finally:
        runtime.shutdown()
