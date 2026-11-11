"""하네스 구조 스모크 테스트(01단계)."""

import importlib

import pytest

HARNESS_PACKAGES = [
    "app",
    "domain",
    "collectors",
    "integrations",
    "security",
    "storage",
    "config",
]


@pytest.mark.parametrize("name", HARNESS_PACKAGES)
def test_harness_package_importable(name: str) -> None:
    """하네스 0-1장의 src 하위 패키지를 모두 import할 수 있어야 한다."""
    assert importlib.import_module(name) is not None
