"""테스트·데모용 가짜 G-Knowledge. 메모리에만 저장하고 외부로 보내지 않는다."""

from __future__ import annotations

from domain.knowledge import (
    KnowledgeHit,
    KnowledgeUnavailableError,
    MaskedDraft,
    RegisterResult,
)
from security.masking import MaskedText

FAKE_BASE = "https://gk.example.invalid/knowledge/"


class FakeKnowledgeStore:
    name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.items: list[MaskedDraft] = []
        self.search_calls = 0

    def register(self, draft: MaskedDraft) -> RegisterResult:
        if self.fail:
            raise KnowledgeUnavailableError("G-Knowledge 등록에 실패했습니다(가짜)")
        self.items.append(draft)
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
