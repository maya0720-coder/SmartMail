"""AI-Ground 질문 문장 만들기와 답변 해석(AI-01·04, 결정기록 0008).

- 질문에는 고정 안내문과 마스킹된 값(`MaskedText`)만 넣는다.
  사람 이름·연락처 같은 값을 직접 넣지 않는다.
- 답변은 정해진 형식으로만 해석하고, 해석하지 못하면 `AiUnavailableError`로 실패시킨다(추측 금지).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from domain.ai import AiAnswer, AiUnavailableError, Classification
from domain.tasks.modules import MODULE_TAGS
from security.masking import MaskedText

MAX_ANSWER_CHARS: Final = 4000
MAX_REFERENCES: Final = 5
PARSE_FAILED: Final = "AI 응답 형식을 해석하지 못했습니다. 잠시 후 다시 시도해 주세요."

_MODULES_TEXT: Final = ", ".join((*MODULE_TAGS, "없음"))

CLASSIFY_TEMPLATE: Final = (
    "다음은 SAP ERP 운영 담당자가 받은 메일의 요약입니다(민감정보는 [마스킹]으로 가림).\n"
    "처리해야 할 업무 요청인지 판단해 주세요. 반드시 아래 형식 3줄로만 답하세요.\n"
    "업무: 예 또는 아니오\n"
    "모듈: {modules} 중 하나\n"
    "확신도: 0과 1 사이 숫자\n"
    "---\n"
    "요약: {summary}"
)

SUGGEST_TEMPLATE: Final = (
    "다음은 SAP ERP 운영 문의의 요약입니다(민감정보는 [마스킹]으로 가림).\n"
    "아래 비슷한 과거 사례를 참고해, 확인 순서와 조치 방법을 5줄 이내로 제안해 주세요.\n"
    "근거 문서가 있으면 마지막 줄에 https 주소만 적어 주세요.\n"
    "---\n"
    "문의 요약: {summary}\n"
    "비슷한 사례:\n{cases}"
)

# 줄 단위로만 찾는다(반복 상한을 둬 처리 시간이 입력 길이에 비례하게 함)
_TASK_LINE: Final = re.compile(
    r"^[ \t*]{0,4}업무[ \t*]{0,3}[:：][ \t]{0,3}(예|아니오)", re.MULTILINE
)
_MODULE_LINE: Final = re.compile(
    r"^[ \t*]{0,4}모듈[ \t*]{0,3}[:：][ \t]{0,3}([A-Za-z가-힣]{1,10})", re.MULTILINE
)
_CONFIDENCE_LINE: Final = re.compile(
    r"^[ \t*]{0,4}확신도[ \t*]{0,3}[:：][ \t]{0,3}([01](?:\.[0-9]{1,3})?)", re.MULTILINE
)
_HTTPS_LINK: Final = re.compile(r"https://[^\s<>\"')\]]{1,500}")


def classify_prompt(summary: MaskedText) -> str:
    return CLASSIFY_TEMPLATE.format(modules=_MODULES_TEXT, summary=summary.value)


def suggest_prompt(summary: MaskedText, cases: Sequence[MaskedText]) -> str:
    lines = "\n".join(f"- {case.value}" for case in cases) or "- (없음)"
    return SUGGEST_TEMPLATE.format(summary=summary.value, cases=lines)


def _module(raw: str | None) -> str | None:
    if raw is None:
        return None
    by_upper = {tag.upper(): tag for tag in MODULE_TAGS}
    return by_upper.get(raw.strip().upper())


def parse_classification(text: str) -> Classification:
    head = text[:MAX_ANSWER_CHARS]
    task = _TASK_LINE.search(head)
    if task is None:
        raise AiUnavailableError(PARSE_FAILED)
    module = _MODULE_LINE.search(head)
    confidence_match = _CONFIDENCE_LINE.search(head)
    confidence = float(confidence_match.group(1)) if confidence_match else 0.5
    return Classification(
        is_task=task.group(1) == "예",
        module_tag=_module(module.group(1) if module else None),
        confidence=min(1.0, max(0.0, confidence)),
    )


def parse_answer(text: str) -> AiAnswer:
    body = text.strip()[:MAX_ANSWER_CHARS]
    if not body:
        raise AiUnavailableError(PARSE_FAILED)
    links = tuple(dict.fromkeys(_HTTPS_LINK.findall(body)))[:MAX_REFERENCES]
    # 링크는 게이트웨이에서 허용 사이트만 남긴다(safe_link)
    return AiAnswer(text=body, references=links)
