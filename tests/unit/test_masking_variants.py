"""마스킹 표기 변형 회귀 테스트(보안 검토 M-1·M-2, AI-03).

값은 실행마다 새로 만든 합성 값을 쓴다(소스에 비밀값 형태를 남기지 않음).
"""

from __future__ import annotations

import secrets

import pytest

from fixtures.synthetic import group4, synthetic_card, synthetic_prefixed_token, synthetic_rrn
from security.masking import (
    LABEL_CARD,
    LABEL_EMAIL,
    LABEL_PHONE,
    LABEL_RRN,
    MaskedText,
    mask,
)


def _value() -> str:
    """숫자가 섞인 임의 값."""
    return "a1" + secrets.token_hex(4)


def _fullwidth(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


SECRET_TEMPLATES = [
    "비밀번호 {v}",
    "비밀번호는{v}",
    "비밀번호가 {v} 입니다",
    "비밀번호를 {v}로 변경",
    "임시 비밀번호 - {v}",
    "비밀번호({v})",
    '비밀번호 "{v}"',
    "인증번호 [{v}]",
    "OTP {v}",
    "PW: {v}",
    "P/W: {v}",
    "패스워드: {v}",
    "private_key={v}",
    '"access_token": "{v}"',
    "{{'password': '{v}'}}",
    "db_password={v}",
    "DB_PASSWORD={v}",
    "userPassword={v}",
    "sessionToken={v}",
    "credential: {v}",
    "sid={v}",
    "Authorization: Basic {v}",
    "Cookie: JSESSIONID={v}",
    "password: '{v} phrase'",
]


@pytest.mark.parametrize("template", SECRET_TEMPLATES)
def test_ai03_secret_variants_masked(template: str) -> None:
    value = _value()
    result = mask(template.format(v=value)).value
    assert value not in result, result


def test_ai03_fullwidth_secret_masked() -> None:
    value = _value()
    assert value not in mask(_fullwidth(f"Password: {value}")).value
    assert value not in mask(f"비밀번호＝{value}").value


@pytest.mark.parametrize("prefix", ["AIza", "github_pat_"])
def test_ai03_more_token_formats_masked(prefix: str) -> None:
    token = synthetic_prefixed_token(prefix, 35 if prefix == "AIza" else 40)
    assert token not in mask(f"키 {token}").value


@pytest.mark.parametrize("sep", [".", "—", "－", "_"])
def test_ai03_rrn_separator_variants(sep: str) -> None:
    rrn = synthetic_rrn().replace("-", sep)
    assert LABEL_RRN in mask(f"번호 {rrn}").value


def test_ai03_fullwidth_rrn() -> None:
    assert LABEL_RRN in mask(_fullwidth(synthetic_rrn())).value


@pytest.mark.parametrize(
    "phone",
    ["(02)123-4567", "02)123-4567", "+82 10 1234 5678", "+82-10-1234-5678", "010–1234–5678"],
)
def test_ai03_phone_variants(phone: str) -> None:
    assert mask(f"연락처 {phone} 입니다").value == f"연락처 {LABEL_PHONE} 입니다"


def test_ai03_fullwidth_phone_labeled_as_phone() -> None:
    assert mask(_fullwidth("010-1234-5678")).value == LABEL_PHONE


def test_ai03_card_variants() -> None:
    digits = synthetic_card()
    for text in (
        group4(digits, "."),
        group4(digits, "  "),
        f"{digits[:4]} {digits[4:8]}-{digits[8:12]}-{digits[12:]}",
        group4(digits[:12] + "****", "-"),
    ):
        result = mask(f"카드 {text}").value
        assert result == f"카드 {LABEL_CARD}", text


def test_ai03_international_email() -> None:
    assert mask("메일 홍길동@예시.한국 확인").value == f"메일 {LABEL_EMAIL} 확인"


@pytest.mark.parametrize(
    "text",
    [
        "비밀번호를 변경해 주세요",
        "monkey: banana",
        "inside: value",
        "opinion: good",
        "암호화는 중요합니다",
    ],
)
def test_ai03_variant_rules_keep_plain_sentences(text: str) -> None:
    assert mask(text).value == text


# --- MaskedText 생성 경로 봉쇄(보안 검토 M-3) ----------------------------------


def test_ai03_masked_text_cannot_be_subclassed() -> None:
    with pytest.raises(TypeError):
        type("Sub", (MaskedText,), {})


def test_ai03_masked_text_cannot_be_reinitialized() -> None:
    import security.masking as module

    masked = mask("hello")
    token = vars(module)["_CONSTRUCT_TOKEN"]
    with pytest.raises(TypeError):
        MaskedText.__init__(masked, "changed", token)
    assert masked.value == "hello"


def test_ai03_forged_instance_without_mask_has_no_value() -> None:
    from security.masking import require_masked

    forged = object.__new__(MaskedText)
    with pytest.raises(AttributeError):
        _ = forged.value
    assert require_masked(mask("x")).value == "x"


def test_ai03_masked_text_copy_and_pickle() -> None:
    import copy
    import pickle

    masked = mask("hello")
    assert copy.copy(masked) is masked
    assert copy.deepcopy(masked) is masked
    with pytest.raises(TypeError):
        pickle.dumps(masked)
