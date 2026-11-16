"""테스트·데모용 가짜 G-Knowledge. 메모리에만 저장하고 외부로 보내지 않는다."""

from __future__ import annotations

import threading

from domain.knowledge import (
    STEP_ANALYZE,
    STEP_OPEN,
    STEP_SUBMIT,
    KnowledgeHit,
    KnowledgeUnavailableError,
    MaskedDraft,
    RegisterNotSentError,
    RegisterResult,
    RegisterResultUnknownError,
    StepCallback,
)
from security.masking import MaskedText

FAKE_BASE = "https://gk.example.invalid/knowledge/"


class FakeKnowledgeStore:
    """fail=True면 등록·검색 모두 실패. register_fail로 등록만 실패 방식을 고른다(결정기록 0035).

    - register_fail="unsent": 등록요청 전 실패(코드 register_fail_code)
    - register_fail="unknown": 등록요청 뒤 결과 미확인(가짜 저장소에는 저장된 것으로 본다)
    - gate: 주면 등록요청 직전에 이 이벤트를 기다린다(테스트에서 진행 중 상태를 붙잡을 때)
    """

    name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.register_fail: str | None = None
        self.register_fail_code = "analyze_timeout"
        self.gate: threading.Event | None = None
        self.items: list[MaskedDraft] = []
        self.search_calls = 0
        self.register_calls = 0

    def register(self, draft: MaskedDraft, on_step: StepCallback | None = None) -> RegisterResult:
        self.register_calls += 1
        step = on_step or (lambda _name: None)
        if self.fail:
            raise RegisterNotSentError("G-Knowledge 등록에 실패했습니다(가짜)", "failed")
        step(STEP_OPEN)
        step(STEP_ANALYZE)
        if self.register_fail == "unsent":
            raise RegisterNotSentError("등록하지 않았습니다(가짜)", self.register_fail_code)
        if self.gate is not None:
            self.gate.wait(timeout=10)
        step(STEP_SUBMIT)
        self.items.append(draft)
        if self.register_fail == "unknown":
            raise RegisterResultUnknownError("등록 결과를 확인하지 못했습니다(가짜)")
        return RegisterResult(FAKE_BASE + str(len(self.items)))

    def search(self, query: MaskedText) -> list[KnowledgeHit]:
        self.search_calls += 1
        if self.fail:
            raise KnowledgeUnavailableError("G-Knowledge 검색에 실패했습니다(가짜)")
        words = [w for w in query.value.split() if len(w) >= 2]
        hits = []
        for index, draft in enumerate(self.items, start=1):
            text = f"{draft.title.value} {draft.body.value}"
            if words and any(w in text for w in words):
                hits.append(
                    KnowledgeHit(draft.title.value, FAKE_BASE + str(index), draft.body.value[:120])
                )
        return hits
