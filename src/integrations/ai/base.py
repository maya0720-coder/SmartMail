"""AI 어댑터 공통 요소. 인터페이스는 `domain.ai`에 있고 여기서 다시 내보낸다."""

from domain.ai import AI_LABEL, AiAnswer, AiUnavailableError, Classification, KnowledgeAI

__all__ = ["AI_LABEL", "AiAnswer", "AiUnavailableError", "Classification", "KnowledgeAI"]
