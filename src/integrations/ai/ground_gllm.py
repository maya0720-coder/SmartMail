"""AI-Ground g-llm 어댑터(AI-01, AI-05).

API 엔드포인트·인증 방식·요청 형식은 확인 필요다. 확인 전에는 추측해서 구현하지 않는다.
로그인 여부만 확인한 뒤 `AiUnavailableError`를 던진다.
로그인은 04단계 로그인 창(Site.AIGROUND)을 쓴다.
"""

from __future__ import annotations

from collections.abc import Sequence

from domain.ai import AiAnswer, AiUnavailableError, Classification
from security.logging_policy import Site
from security.masking import MaskedText
from security.sessions import SessionStore

NOT_READY_MESSAGE = "AI-Ground g-llm 연동 규격이 확인되지 않아 아직 호출할 수 없습니다(확인 필요)"
LOGIN_REQUIRED_MESSAGE = "AI-Ground 로그인이 필요합니다"


class GroundGllmAdapter:
    name = "ai-ground-gllm"

    def __init__(self, base_url: str, sessions: SessionStore) -> None:
        self._base_url = base_url  # 설정값(SMARTMAIL_AIGROUND_BASE_URL)
        self._sessions = sessions

    def _require_login(self) -> None:
        if self._sessions.current(Site.AIGROUND) is None:
            raise AiUnavailableError(LOGIN_REQUIRED_MESSAGE)

    def classify_task(self, summary: MaskedText) -> Classification:
        self._require_login()
        raise AiUnavailableError(NOT_READY_MESSAGE)

    def suggest_solution(
        self, summary: MaskedText, similar_cases: Sequence[MaskedText]
    ) -> AiAnswer:
        self._require_login()
        raise AiUnavailableError(NOT_READY_MESSAGE)
