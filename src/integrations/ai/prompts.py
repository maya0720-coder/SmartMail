"""AI-Ground 질문 문장 만들기와 답변 해석(AI-01·04, 결정기록 0008).

- 질문에는 고정 안내문과 마스킹된 값(`MaskedText`)만 넣는다.
  사람 이름·연락처 같은 값을 직접 넣지 않는다.
- 답변은 정해진 형식으로만 해석하고, 해석하지 못하면 `AiUnavailableError`로 실패시킨다(추측 금지).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from domain.ai import AiAnswer, AiParseError, Classification, RequestDigest
from domain.tasks.modules import MODULE_TAGS
from security.masking import MaskedText

MAX_ANSWER_CHARS: Final = 4000
MAX_REFERENCES: Final = 5
PARSE_FAILED: Final = "AI 응답 형식을 해석하지 못했습니다. 잠시 후 다시 시도해 주세요."


CLASSIFY_TEMPLATE: Final = (
    "다음은 업무 담당자가 받은 메일의 요약입니다(민감정보는 [마스킹]으로 가림).\n"
    "처리해야 할 업무 요청인지 판단해 주세요. 반드시 아래 형식 2줄로만 답하세요.\n"
    "업무: 예 또는 아니오\n"
    "확신도: 0과 1 사이 숫자\n"
    "---\n"
    "요약: {summary}"
)

SUGGEST_TEMPLATE: Final = (
    "당신은 SAP ERP 운영 담당자를 돕는 조수입니다. 아래는 담당자가 받은 업무 요청입니다"
    "(민감정보는 [마스킹]으로 가림).\n"
    "담당자가 바로 처리할 수 있게 해결 방안을 제안해 주세요. 비슷한 과거 사례가 있으면 "
    "그 원인·조치를 우선 참고하고, 사례와 다른 점이 있으면 짚어 주세요. "
    "확실하지 않은 내용은 '추정'이라고 쓰세요.\n"
    "반드시 아래 형식으로만 답하세요(각 항목 3줄 이내).\n"
    "원인 추정: ...\n"
    "확인 순서:\n1. ...\n2. ...\n"
    "조치 방안:\n- ...\n"
    "관련 T-code: 쉼표로 구분(없으면 없음)\n"
    "참고한 사례: 사례 번호(없으면 없음)\n"
    "근거 문서가 있으면 마지막 줄에 https 주소만 적어 주세요.\n"
    "---\n"
    "업무 요청:\n{summary}\n"
    "---\n"
    "비슷한 과거 사례:\n{cases}"
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


SUMMARIZE_TEMPLATE: Final = (
    "당신은 SAP ERP 운영 담당자의 메일함을 정리하는 조수입니다. 다음은 담당자가 받은 메일 "
    "{count}건입니다(민감정보는 [마스킹]으로 가림). 전달(FW) 메일은 인용된 원래 메일의 요청이 "
    "핵심이고, '후속 메일'이 있으면 추가·변경된 요청을 함께 반영하세요.\n"
    "메일마다 아래 4줄 형식으로만 답하세요.\n"
    "[메일 번호]\n"
    "제목: 무엇을(시스템·문서·T-code·화면) + 어떻게(행동), 명사형 40자 이내\n"
    "요청: 요청 사항을 ' / '로 구분(최대 5개, 각 40자 이내)\n"
    "기한: 메일에 적힌 기한 그대로(없으면 없음)\n"
    "조치: 담당자가 할 일 한 문장(60자 이내)\n"
    "규칙: 인사말·서명·면책 문구는 무시하고, 사람 이름은 쓰지 마세요. "
    "'부탁드립니다', '확인 부탁' 같은 요청 어미만으로 제목을 쓰지 마세요.\n"
    "예시)\n[메일 1]\n제목: KSB1 원가센터 보고서 조회 권한 부여\n"
    "요청: 원가팀 사용자 KSB1 조회 권한 부여 / 권한 범위 원가센터 그룹 확인\n"
    "기한: 금일 중\n조치: 원가 조회 역할(PFCG) 확인 후 사용자에 할당\n"
    "---\n"
    "{items}"
)
MAX_SUMMARY_LINE: Final = 120
MAX_FIELD_LINE: Final = 200
# 묶음 머리: '[메일 1]'뿐 아니라 실제 채팅 답변에 흔한 꼴도 받는다(결정기록 0026).
#   '**[메일 1]**', '### 메일 1', '1. [메일 1]', '메일 1:', '[메일 1] 제목: …'(같은 줄에 제목)
# 줄 맨 앞(장식·번호 뒤)에서만 찾고 숫자 뒤에 글자가 붙으면('메일 1건') 머리로 보지 않는다.
_ITEM_HEAD: Final = re.compile(
    r"^[ \t*#>-]{0,6}(?:\d{1,2}[.)][ \t]{0,2})?[*\[(]{0,3}메일[ \t]{0,2}(\d{1,2})(?![\d가-힣])"
    r"[ \t]{0,2}[\])*]{0,3}[ \t*]{0,4}[:：.]?[ \t]{0,2}",
    re.MULTILINE,
)


def _field(block: str, name: str) -> str | None:
    match = re.search(
        rf"^[ \t*-]{{0,4}}{name}[ \t*]{{0,3}}[:：][ \t]{{0,3}}(.{{1,400}})$", block, re.M
    )
    if match is None:
        return None
    value = match.group(1).strip().strip("*\"'“”").strip()
    return value[:MAX_FIELD_LINE] or None


def summarize_prompt(items: Sequence[MaskedText]) -> str:
    body = "\n\n".join(f"[메일 {index}]\n{item.value}" for index, item in enumerate(items, 1))
    return SUMMARIZE_TEMPLATE.format(count=len(items), items=body)


def parse_digests(text: str, count: int) -> list[RequestDigest | None]:
    """'[메일 N]' 묶음마다 제목·요청·기한·조치 줄을 해석한다. 제목이 없으면 None(추측 금지)."""
    head = text[: MAX_ANSWER_CHARS * 2]
    marks = list(_ITEM_HEAD.finditer(head))
    found: dict[int, RequestDigest] = {}
    for index, mark in enumerate(marks):
        number = int(mark.group(1))
        end = marks[index + 1].start() if index + 1 < len(marks) else len(head)
        # 같은 줄에 이어진 내용('[메일 1] 제목: …')도 한 줄로 읽히게 줄바꿈을 앞에 붙인다
        block = "\n" + head[mark.end() : end]
        title = _field(block, "제목")
        if not (1 <= number <= count) or number in found or not title:
            continue
        requests = _field(block, "요청") or ""
        due = _field(block, "기한")
        found[number] = RequestDigest(
            title=title[:MAX_SUMMARY_LINE],
            requests=tuple(r.strip()[:80] for r in requests.split(" / ") if r.strip())[:5],
            due=None if due in (None, "없음", "없음.", "-") else due,
            action=_field(block, "조치"),
        )
    if not found:
        raise AiParseError(PARSE_FAILED)
    return [found.get(index) for index in range(1, count + 1)]


def classify_prompt(summary: MaskedText) -> str:
    return CLASSIFY_TEMPLATE.format(summary=summary.value)


def suggest_prompt(summary: MaskedText, cases: Sequence[MaskedText]) -> str:
    lines = "\n".join(f"[사례 {n}] {case.value}" for n, case in enumerate(cases, 1)) or "(없음)"
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
        raise AiParseError(PARSE_FAILED)
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
        raise AiParseError(PARSE_FAILED)
    links = tuple(dict.fromkeys(_HTTPS_LINK.findall(body)))[:MAX_REFERENCES]
    # 링크는 게이트웨이에서 허용 사이트만 남긴다(safe_link)
    return AiAnswer(text=body, references=links)
