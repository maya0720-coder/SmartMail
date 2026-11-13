"""제안 근거 보기: 원본 메일 앞부분과 요청 문장 위치(결정기록 0015, 명세 2-1).

원본은 볼 때마다 다시 읽어 메모리에서만 쓴다(MAIL-08). 이 모듈은 저장·로그를 하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from domain.tasks.extract import REQUEST_PATTERNS
from domain.tasks.summarize import QUOTE_START

MAX_LINES: Final = 12
MAX_LINE_CHARS: Final = 300
_SENTENCE: Final = re.compile(r"[^.!?。]*[.!?。]?")


@dataclass(frozen=True, slots=True)
class Highlight:
    line: int  # lines 안의 줄 번호(0부터)
    start: int  # 그 줄 안의 시작 위치
    end: int  # 끝 위치(포함하지 않음)


def context_lines(body: str) -> tuple[list[str], Highlight | None]:
    """회신 기록 앞까지의 본문(빈 줄 제외, 최대 12줄)과 첫 요청 문장 위치."""
    lines: list[str] = []
    for raw in body.splitlines():
        if QUOTE_START.match(raw):
            break
        line = raw.strip()
        if line:
            lines.append(line[:MAX_LINE_CHARS])
        if len(lines) >= MAX_LINES:
            break
    for index, line in enumerate(lines):
        match = REQUEST_PATTERNS.search(line)
        if match is None:
            continue
        # 요청 표현이 들어 있는 문장 전체를 표시한다
        for sentence in _SENTENCE.finditer(line):
            if sentence.start() <= match.start() < sentence.end() and sentence.group().strip():
                text = sentence.group()
                start = sentence.start() + (len(text) - len(text.lstrip()))
                return lines, Highlight(index, start, sentence.start() + len(text.rstrip()))
        return lines, Highlight(index, match.start(), match.end())
    return lines, None
