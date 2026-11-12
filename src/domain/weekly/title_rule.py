"""위키 주간보고 페이지 제목 규칙(WIKI-02). 화면에서 바꿀 수 있으므로 입력을 엄격히 검증한다.

제목 = 형식 문자열의 날짜 변수를 '주 시작일(목) + 날짜 보정(0~6일)' 날짜로 바꾼 값.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Final

from domain.errors import UserInputError

MAX_FORMAT_CHARS: Final = 60
MAX_OFFSET_DAYS: Final = 6
# 주 시작(목) 기준 보정일 → 요일 이름(화면 표시용)
OFFSET_WEEKDAY_LABELS: Final = ("목", "금", "토", "일", "월", "화", "수")

_PLACEHOLDERS: Final = {
    "{yyyymmdd}": "%Y%m%d",
    "{yymmdd}": "%y%m%d",
    "{yyyy}": "%Y",
    "{yy}": "%y",
    "{mm}": "%m",
    "{dd}": "%d",
}
# 주마다 제목이 달라지려면 일(日)이 들어간 변수가 있어야 한다.
_DAY_PLACEHOLDERS: Final = ("{yyyymmdd}", "{yymmdd}", "{dd}")
_TOKEN: Final = re.compile(r"\{[a-z]+\}")
# 변수를 뺀 나머지 글자: 한글·영문·숫자·공백·_-.() 만 허용(위키 제목 금지 문자·마크업 차단)
_LITERAL: Final = re.compile(r"[0-9A-Za-z가-힣 _\-.()]*")


def validate_title_rule(page_title_format: str, offset_days: int) -> None:
    """형식·보정일이 올바르지 않으면 UserInputError."""
    if not 0 <= offset_days <= MAX_OFFSET_DAYS:
        raise UserInputError("날짜 기준 요일이 올바르지 않습니다")
    text = page_title_format.strip()
    if not text or len(text) > MAX_FORMAT_CHARS or text != page_title_format:
        raise UserInputError(f"제목 형식은 앞뒤 공백 없이 1~{MAX_FORMAT_CHARS}자여야 합니다")
    unknown = [t for t in _TOKEN.findall(text) if t not in _PLACEHOLDERS]
    if unknown:
        raise UserInputError("지원하지 않는 날짜 변수가 있습니다")
    if not any(p in text for p in _DAY_PLACEHOLDERS):
        raise UserInputError("제목에 {yymmdd}·{yyyymmdd}·{dd} 중 하나가 있어야 합니다")
    literal = _TOKEN.sub("", text)
    if not _LITERAL.fullmatch(literal):
        raise UserInputError("제목에는 한글·영문·숫자·공백·_ - . ( ) 만 쓸 수 있습니다")


def render_title(page_title_format: str, day: datetime) -> str:
    title = page_title_format
    for token, pattern in _PLACEHOLDERS.items():
        title = title.replace(token, day.strftime(pattern))
    return title
