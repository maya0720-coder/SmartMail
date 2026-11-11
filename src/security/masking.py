"""민감정보 마스킹 게이트웨이(AI-03, GK-07, 기획서 5장).

외부(위키·G-Knowledge·AI-Ground)로 보내는 함수는 `str`이 아니라 `MaskedText`만
인자로 받는다. `MaskedText`는 `mask()`로만 만들도록 설계해 마스킹 누락을 타입 검사(mypy)와
실행 시점 검사(`require_masked`) 양쪽에서 막는다.

한계: Python에서 생성 경로를 완전히 봉쇄할 수는 없다(모듈 내부 토큰 직접 사용 등 의도적 우회).
그런 코드는 `scripts/check.py`의 금지 패턴 검사로 막는다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Final, NoReturn, final

from security import mask_patterns as p
from security.mask_patterns import (
    LABEL_ACCOUNT,
    LABEL_CARD,
    LABEL_EMAIL,
    LABEL_PHONE,
    LABEL_RRN,
    LABEL_SECRET,
    LABEL_TOKEN,
)

__all__ = [
    "LABEL_ACCOUNT",
    "LABEL_CARD",
    "LABEL_EMAIL",
    "LABEL_PHONE",
    "LABEL_RRN",
    "LABEL_SECRET",
    "LABEL_TOKEN",
    "MAX_MASK_INPUT_CHARS",
    "MaskedText",
    "mask",
    "require_masked",
]

# 마스킹 입력 최대 길이(문자). 초과하면 마스킹하지 않은 채 넘기지 않고 거부한다.
MAX_MASK_INPUT_CHARS: Final = 2_000_000

_CONSTRUCT_TOKEN: Final = object()
_IMMUTABLE_MESSAGE: Final = "MaskedText는 변경할 수 없습니다"


@final
class MaskedText:
    """마스킹을 거친 문자열. `mask()`로만 생성되는 불변 값 객체."""

    __slots__ = ("_value",)
    _value: str

    def __init__(self, value: str, _token: object = None) -> None:
        if _token is not _CONSTRUCT_TOKEN:
            raise TypeError("MaskedText는 mask()로만 만들 수 있습니다")
        try:
            object.__getattribute__(self, "_value")
        except AttributeError:
            object.__setattr__(self, "_value", value)
            return
        raise TypeError(_IMMUTABLE_MESSAGE)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError("MaskedText는 상속할 수 없습니다")

    @property
    def value(self) -> str:
        return self._value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"MaskedText(len={len(self._value)})"

    def __eq__(self, other: object) -> bool:
        return type(other) is MaskedText and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __copy__(self) -> MaskedText:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> MaskedText:
        return self

    def __reduce__(self) -> NoReturn:
        # 직렬화 후 복원 경로로 임의 값을 넣지 못하게 막는다.
        raise TypeError("MaskedText는 직렬화할 수 없습니다. .value를 저장하세요")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError(_IMMUTABLE_MESSAGE)

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError(_IMMUTABLE_MESSAGE)


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2 == 1:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _keep_key(label: str) -> Callable[[re.Match[str]], str]:
    def replace(match: re.Match[str]) -> str:
        return f"{match.group('key')}{match.group('sep')}{label}"

    return replace


def _mask_plain_card(match: re.Match[str]) -> str:
    return LABEL_CARD if _luhn_valid(match.group(0)) else match.group(0)


def _mask_grouped_account(match: re.Match[str]) -> str:
    digit_count = sum(char.isdigit() for char in match.group(0))
    if p.ACCOUNT_MIN_DIGITS <= digit_count <= p.ACCOUNT_MAX_DIGITS:
        return LABEL_ACCOUNT
    return match.group(0)


def mask(text: str, *, mask_email: bool = True) -> MaskedText:
    """민감정보를 라벨로 치환한 `MaskedText`를 돌려준다.

    입력은 NFKC 정규화(전각 → 반각)한 뒤 마스킹하므로 결과 문자열도 정규화된 형태다.

    Raises:
        TypeError: text가 str이 아닐 때
        ValueError: 길이가 MAX_MASK_INPUT_CHARS를 넘을 때(마스킹 없이 통과시키지 않는다)
    """
    if not isinstance(text, str):
        raise TypeError("마스킹 대상은 문자열이어야 합니다")
    if len(text) > MAX_MASK_INPUT_CHARS:
        raise ValueError(f"마스킹 입력이 최대 길이({MAX_MASK_INPUT_CHARS}자)를 넘었습니다")

    result = unicodedata.normalize("NFKC", text)
    # 문맥 기반(키워드 + 값)을 먼저 적용해 키워드는 남기고 값만 가린다.
    result = p.AUTH_HEADER.sub(_keep_key(LABEL_TOKEN), result)
    result = p.SECRET_CONTEXT.sub(_keep_key(LABEL_SECRET), result)
    result = p.SECRET_SPACED.sub(_keep_key(LABEL_SECRET), result)
    result = p.TOKEN_CONTEXT.sub(_keep_key(LABEL_TOKEN), result)
    result = p.BEARER.sub(_keep_key(LABEL_TOKEN), result)
    result = p.KNOWN_TOKEN.sub(LABEL_TOKEN, result)
    # 형식 기반: 구체적인 패턴(주민번호·카드)을 먼저, 일반 숫자 묶음(계좌)을 나중에.
    result = p.RRN.sub(LABEL_RRN, result)
    result = p.CARD_GROUPED.sub(LABEL_CARD, result)
    result = p.CARD_PLAIN.sub(_mask_plain_card, result)
    result = p.PHONE.sub(LABEL_PHONE, result)
    result = p.ACCOUNT_CONTEXT.sub(_keep_key(LABEL_ACCOUNT), result)
    result = p.ACCOUNT_GROUPED.sub(_mask_grouped_account, result)
    if mask_email:
        result = p.EMAIL.sub(LABEL_EMAIL, result)
    return MaskedText(result, _CONSTRUCT_TOKEN)


def require_masked(value: object) -> MaskedText:
    """외부 전송 경계에서 실행 시점에도 `MaskedText`인지 확인한다(하위 클래스 불허)."""
    if type(value) is not MaskedText:
        raise TypeError("외부 전송 데이터는 mask()를 거친 MaskedText여야 합니다")
    return value
