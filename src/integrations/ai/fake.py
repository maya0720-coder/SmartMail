"""테스트·데모용 가짜 지식 AI. 외부로 아무것도 보내지 않고 고정 규칙으로 답한다."""

from __future__ import annotations

import time
from collections.abc import Sequence

from domain.ai import AiAnswer, AiUnavailableError, Classification
from domain.tasks.modules import guess_module
from security.masking import MaskedText


class FakeKnowledgeAI:
    name = "fake"

    def __init__(self, *, fail: bool = False, delay_seconds: float = 0.0) -> None:
        self.fail = fail
        self.delay_seconds = delay_seconds
        self.received: list[MaskedText] = []

    def _maybe_fail(self) -> None:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.fail:
            raise AiUnavailableError("AI 응답 오류(가짜)")

    def classify_task(self, summary: MaskedText) -> Classification:
        self.received.append(summary)
        self._maybe_fail()
        text = summary.value
        is_task = any(word in text for word in ("요청", "부탁", "확인", "처리", "필요"))
        return Classification(is_task, guess_module(text), 0.6 if is_task else 0.3)

    def suggest_solution(
        self, summary: MaskedText, similar_cases: Sequence[MaskedText]
    ) -> AiAnswer:
        self.received.append(summary)
        self.received.extend(similar_cases)
        self._maybe_fail()
        module = guess_module(summary.value) or "일반"
        return AiAnswer(
            text=(
                f"[{module}] 관련 설정과 최근 변경 이력을 먼저 확인하세요. "
                "오류 메시지 번호로 SAP 노트를 검색하고, 테스트 환경에서 재현한 뒤 조치하세요."
            ),
            references=(),
        )
