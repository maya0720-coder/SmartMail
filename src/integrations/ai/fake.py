"""테스트·데모용 가짜 지식 AI. 외부로 아무것도 보내지 않고 고정 규칙으로 답한다."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from typing import Final

from domain.ai import AiAnswer, AiUnavailableError, Classification
from domain.tasks.modules import guess_module
from security.masking import MaskedText

_REQUEST: Final = re.compile(r"요청|부탁|확인|처리|검토|바랍니다|주세요")


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

    def summarize_requests(self, items: Sequence[MaskedText]) -> list[str | None]:
        """본문에서 요청 표현이 있는 첫 문장을 40자로 줄여 요약으로 쓴다(외부 전송 없음)."""
        self.received.extend(items)
        self._maybe_fail()
        result: list[str | None] = []
        for item in items:
            text = item.value
            body = text.split("본문:", 1)[1] if "본문:" in text else text
            sentences = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", body) if s.strip()]
            pick = next(
                (s for s in sentences if _REQUEST.search(s)), sentences[0] if sentences else ""
            )
            result.append(pick[:40] or None)
        return result

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
