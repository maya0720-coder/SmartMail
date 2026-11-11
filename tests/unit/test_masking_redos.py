"""마스킹 정규식 ReDoS 회귀 테스트(보안 검토 H-1).

적대적 반복 입력에서 처리 시간이 길이에 비례(선형)하는지 확인한다.
길이를 4배로 늘렸을 때 시간이 제곱(16배)에 가깝게 늘지 않아야 하고, 절대 시간도 제한한다.
"""

from __future__ import annotations

import time

import pytest

from security.masking import mask

BASE_LENGTH = 20_000
# 선형이면 약 4배, 제곱이면 약 16배. 측정 잡음을 감안해 8배를 상한으로 둔다.
MAX_GROWTH_RATIO = 8.0
MAX_SECONDS_AT_LARGE = 2.0

ADVERSARIAL_UNITS = [
    "%",
    "a%",
    "eyJ-",
    "eyJaaaaaaaaa.",
    "a.",
    "-",
    "1-",
    "1 ",
    "password=",
    "비밀번호 ",
    "Bearer ",
    "a@",
    "@a.",
    "(02)",
    "+82 ",
    "1234 ",
    "*",
    "'",
    '"',
]


def _elapsed(text: str) -> float:
    """3회 중 최솟값(전체 테스트 실행 중 다른 스레드·GC로 인한 측정 잡음 완화)."""
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        mask(text)
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("unit", ADVERSARIAL_UNITS)
def test_ai03_mask_runs_in_linear_time(unit: str) -> None:
    small = (unit * BASE_LENGTH)[:BASE_LENGTH]
    large = (unit * BASE_LENGTH * 4)[: BASE_LENGTH * 4]
    small_time = max(_elapsed(small), 1e-3)
    large_time = _elapsed(large)
    assert large_time < MAX_SECONDS_AT_LARGE, f"{unit!r}: {large_time:.2f}s"
    assert large_time / small_time < MAX_GROWTH_RATIO, f"{unit!r}: {large_time / small_time:.1f}x"
