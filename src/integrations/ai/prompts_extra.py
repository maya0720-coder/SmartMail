"""AI-Ground 추가 질문: 도메인 → 회사명, 업무 진행 요약(결정기록 0027·0028).

- 회사명 질문에는 도메인 문자열 하나만 넣는다(이름·주소·제목 없음, 결정 D1).
- 진행 요약 질문에는 메일별 마스킹된 요약(방향·시각·요약 제목·구조화 요약)만 넣는다. 원문 없음.
- 답변은 정해진 형식으로만 해석한다(추측 금지). 해석하지 못하면 AiParseError.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Final

from domain.ai import AiParseError
from integrations.ai.prompts import MAX_ANSWER_CHARS, PARSE_FAILED
from security.masking import MaskedText

COMPANY_TEMPLATE: Final = (
    "다음 인터넷 도메인을 메일 주소로 쓰는 회사(기관)의 한국어 이름을 알려 주세요.\n"
    "확실하지 않으면 company 값을 빈 문자열로 두세요. 설명 없이 JSON 한 줄로만 답하세요.\n"
    '형식: {{"company": "회사 이름"}}\n'
    "---\n"
    "도메인: {domain}"
)

PROGRESS_TEMPLATE: Final = (
    "다음은 업무 담당자의 메일 스레드를 시간순으로 정리한 요약입니다(민감정보는 [마스킹]으로 가림, "
    "받음=담당자가 받은 메일, 보냄=담당자가 보낸 메일).\n"
    "지금까지의 진행 상황을 아래 형식 3줄로만 답하세요(각 줄 120자 이내, 사람 이름 쓰지 않기).\n"
    "요청 사항: 처음 요청과 추가·변경된 요청\n"
    "처리 경과: 담당자가 지금까지 한 일(회신·조치)\n"
    "남은 일: 아직 남은 일(없으면 없음)\n"
    "---\n"
    "{items}"
)
PROGRESS_FIELDS: Final = ("요청 사항", "처리 경과", "남은 일")
MAX_PROGRESS_LINE: Final = 300
_JSON_OBJECT: Final = re.compile(r"\{[^{}]{0,400}\}")


def company_prompt(domain: MaskedText) -> str:
    return COMPANY_TEMPLATE.format(domain=domain.value)


def parse_company(text: str) -> str | None:
    """JSON 답변에서 회사명만 꺼낸다. 형식이 아니면 AiParseError, 모르면 None."""
    for match in _JSON_OBJECT.finditer(text[:MAX_ANSWER_CHARS]):
        try:
            data = json.loads(match.group(0))
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("company"), str):
            return data["company"].strip() or None
    raise AiParseError(PARSE_FAILED)


def progress_prompt(items: Sequence[MaskedText]) -> str:
    body = "\n".join(f"[{index}] {item.value}" for index, item in enumerate(items, 1))
    return PROGRESS_TEMPLATE.format(items=body)


def parse_progress(text: str) -> str:
    """'요청 사항/처리 경과/남은 일' 3줄을 찾는다. 하나도 없으면 AiParseError."""
    head = text[:MAX_ANSWER_CHARS]
    lines = []
    for name in PROGRESS_FIELDS:
        match = re.search(
            rf"^[ \t*#-]{{0,4}}{name}[ \t*]{{0,3}}[:：][ \t]{{0,3}}(.{{1,400}})$", head, re.M
        )
        if match is not None:
            value = match.group(1).strip().strip("*").strip()[:MAX_PROGRESS_LINE]
            lines.append(f"{name}: {value}")
    if not lines:
        raise AiParseError(PARSE_FAILED)
    return "\n".join(lines)
