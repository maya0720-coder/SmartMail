"""마스킹 게이트웨이 테스트(AI-03, GK-07)."""

from __future__ import annotations

import time

import pytest

from fixtures.synthetic import (
    group4,
    synthetic_card,
    synthetic_card_invalid,
    synthetic_jwt,
    synthetic_prefixed_token,
    synthetic_rrn,
)
from security.masking import (
    LABEL_ACCOUNT,
    LABEL_CARD,
    LABEL_EMAIL,
    LABEL_PHONE,
    LABEL_RRN,
    LABEL_SECRET,
    LABEL_TOKEN,
    MAX_MASK_INPUT_CHARS,
    MaskedText,
    mask,
    require_masked,
)

# --- 대상 패턴은 마스킹된다 ------------------------------------------------


def test_ai03_rrn_masked() -> None:
    rrn = synthetic_rrn()
    result = mask(f"홍길동 주민번호 {rrn} 입니다").value
    assert rrn not in result
    assert LABEL_RRN in result


def test_ai03_rrn_without_hyphen_masked() -> None:
    rrn = synthetic_rrn().replace("-", "")
    assert mask(f"번호 {rrn}").value == f"번호 {LABEL_RRN}"


@pytest.mark.parametrize("sep", ["-", " "])
def test_ai03_grouped_card_masked(sep: str) -> None:
    card = group4(synthetic_card(), sep)
    result = mask(f"카드 {card} 결제").value
    assert result == f"카드 {LABEL_CARD} 결제"


def test_ai03_plain_card_with_valid_luhn_masked() -> None:
    card = synthetic_card()
    assert mask(f"카드:{card}").value == f"카드:{LABEL_CARD}"


@pytest.mark.parametrize(
    "phone",
    ["010-1234-5678", "01012345678", "010 1234 5678", "02-123-4567", "031-1234-5678", "1588-1234"],
)
def test_ai03_phone_masked(phone: str) -> None:
    assert mask(f"연락처 {phone}로 연락").value == f"연락처 {LABEL_PHONE}로 연락"


@pytest.mark.parametrize("account", ["110-123-456789", "1002-123-456789", "123-456789-01-234"])
def test_ai03_grouped_account_masked(account: str) -> None:
    assert mask(f"입금 {account} 확인").value == f"입금 {LABEL_ACCOUNT} 확인"


def test_ai03_account_context_masked() -> None:
    assert mask("계좌번호: 12345678901234").value == f"계좌번호: {LABEL_ACCOUNT}"


def test_ai03_email_masked_by_default_and_optional() -> None:
    text = "담당 kim.gasang@example.com 에게"
    assert mask(text).value == f"담당 {LABEL_EMAIL} 에게"
    assert mask(text, mask_email=False).value == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("password=abc123!", f"password={LABEL_SECRET}"),
        ("PWD: qwer1234", f"PWD: {LABEL_SECRET}"),
        ("비밀번호: 가나다1234", f"비밀번호: {LABEL_SECRET}"),
        ("비밀번호는 test1234 입니다", f"비밀번호는 {LABEL_SECRET} 입니다"),
        ("인증번호 = 482913", f"인증번호 = {LABEL_SECRET}"),
        ("새비밀번호:zz99", f"새비밀번호:{LABEL_SECRET}"),
    ],
)
def test_ai03_secret_context_masked(text: str, expected: str) -> None:
    assert mask(text).value == expected


def test_ai03_token_context_masked() -> None:
    assert mask("api_key=abcdEFGH1234").value == f"api_key={LABEL_TOKEN}"
    assert mask("access-token: zzz.yyy").value == f"access-token: {LABEL_TOKEN}"


def test_ai03_bearer_masked() -> None:
    value = synthetic_prefixed_token("", 24)
    assert mask(f"Authorization: Bearer {value}").value == f"Authorization: {LABEL_TOKEN}"
    assert mask(f"헤더 Bearer {value}").value == f"헤더 Bearer {LABEL_TOKEN}"


@pytest.mark.parametrize("prefix", ["sk-", "ghp_", "AKIA"])
def test_ai03_known_token_formats_masked(prefix: str) -> None:
    length = 16 if prefix == "AKIA" else 40
    token = synthetic_prefixed_token(prefix, length)
    if prefix == "AKIA":
        token = token.upper()
    result = mask(f"키 {token} 사용").value
    assert token not in result
    assert LABEL_TOKEN in result


def test_ai03_jwt_masked() -> None:
    jwt = synthetic_jwt()
    assert mask(f"jwt {jwt}").value == f"jwt {LABEL_TOKEN}"


def test_ai03_multiple_items_in_one_text() -> None:
    text = f"홍길동 {synthetic_rrn()} / 010-9876-5432 / password: x1"
    result = mask(text).value
    assert result == f"홍길동 {LABEL_RRN} / {LABEL_PHONE} / password: {LABEL_SECRET}"


# --- 비슷하지만 대상이 아닌 문자열은 유지된다 ------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "완료일 2026-11-11 처리",
        "T-code ME21N, 프로그램 ZMM_PO_001",
        "버전 1.2.3, 수량 1,234,567개",
        "주문번호 4500012345",
        "invalid month 901301-1000000",
        "암호화 방식 검토",
        "비밀번호를 변경해 주세요",
        "opinion: good",
        "tokenizer 설정",
        "sk-short",
        "업무 ID 12-34",
    ],
)
def test_ai03_non_target_text_preserved(text: str) -> None:
    assert mask(text).value == text


def test_ai03_plain_digits_failing_luhn_preserved() -> None:
    digits = synthetic_card_invalid()
    assert mask(f"번호 {digits}").value == f"번호 {digits}"


# --- 경계 조건 ---------------------------------------------------------------


def test_ai03_empty_string() -> None:
    assert mask("").value == ""


def test_ai03_very_long_text_masked_quickly() -> None:
    chunk = "일반 텍스트 010-1234-5678 password=abc " * 1000
    text = (chunk * ((MAX_MASK_INPUT_CHARS // len(chunk)) or 1))[:MAX_MASK_INPUT_CHARS]
    started = time.perf_counter()
    result = mask(text).value
    assert "010-1234-5678" not in result
    assert time.perf_counter() - started < 10


def test_ai03_too_long_text_rejected() -> None:
    with pytest.raises(ValueError, match="최대 길이"):
        mask("a" * (MAX_MASK_INPUT_CHARS + 1))


def test_ai03_non_str_rejected() -> None:
    with pytest.raises(TypeError):
        mask(12345)  # type: ignore[arg-type]


# --- MaskedText 타입 ---------------------------------------------------------


def test_ai03_masked_text_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError):
        MaskedText("raw")


def test_ai03_masked_text_is_immutable() -> None:
    masked = mask("hello")
    with pytest.raises(AttributeError):
        masked._value = "changed"
    with pytest.raises(AttributeError):
        del masked._value


def test_ai03_masked_text_repr_hides_content() -> None:
    assert "password" not in repr(mask("password=abc"))


def test_ai03_require_masked_rejects_plain_str() -> None:
    with pytest.raises(TypeError):
        require_masked("raw text")
    masked = mask("ok")
    assert require_masked(masked) is masked
