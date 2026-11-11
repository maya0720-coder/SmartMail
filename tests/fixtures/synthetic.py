"""테스트용 합성 민감정보 생성기.

실제 개인정보·자격증명을 소스에 넣지 않기 위해, 형식만 맞춘 가짜 값을 실행 시점에 만든다.
생성 값은 실제 사람·카드·계정과 무관하다(카드 발급사 접두사 9는 실사용되지 않는 범위).
"""

from __future__ import annotations


def luhn_check_digit(body: str) -> str:
    """body 뒤에 붙일 Luhn 검증 숫자를 계산한다."""
    total = 0
    for index, char in enumerate(reversed(body)):
        number = int(char)
        if index % 2 == 0:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return str((10 - total % 10) % 10)


def synthetic_card(body: str = "999912345678901") -> str:
    """Luhn 검증을 통과하는 가짜 카드번호(구분자 없음)."""
    return body + luhn_check_digit(body)


def synthetic_card_invalid(body: str = "999912345678901") -> str:
    """Luhn 검증에 실패하는 같은 길이의 숫자열."""
    good = int(luhn_check_digit(body))
    return body + str((good + 1) % 10)


def group4(digits: str, sep: str = "-") -> str:
    return sep.join(digits[i : i + 4] for i in range(0, len(digits), 4))


def synthetic_rrn(birth: str = "900101", gender: str = "1", serial: str = "000000") -> str:
    """주민등록번호 형식의 가짜 값."""
    return f"{birth}-{gender}{serial}"


def synthetic_prefixed_token(prefix: str, length: int = 40) -> str:
    """접두사 기반 토큰 형식의 가짜 값(문자 반복)."""
    return prefix + ("A1b2" * length)[:length]


def synthetic_jwt() -> str:
    """JWT 형식(헤더.페이로드.서명)의 가짜 값."""
    return ".".join(["eyJ" + "x" * 12, "y" * 16, "z" * 16])
