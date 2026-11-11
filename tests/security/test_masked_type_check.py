"""MaskedText를 거치지 않은 문자열을 전송 함수에 넣으면 mypy가 실패하는지 확인(AI-03, GK-07)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mypy import api as mypy_api

ROOT = Path(__file__).resolve().parents[2]

SNIPPET_HEADER = """
from security.masking import MaskedText, mask

def send_to_external(payload: MaskedText) -> None:
    ...
"""


def _run_mypy(tmp_path: Path, body: str) -> tuple[int, str]:
    target = tmp_path / "snippet.py"
    target.write_text(SNIPPET_HEADER + body, encoding="utf-8")
    stdout, _stderr, status = mypy_api.run(
        [
            "--config-file",
            str(ROOT / "pyproject.toml"),
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(target),
        ]
    )
    return status, stdout


@pytest.mark.slow
def test_ai03_plain_str_rejected_by_type_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    status, output = _run_mypy(tmp_path, 'send_to_external("raw text")\n')
    assert status != 0
    assert "arg-type" in output


@pytest.mark.slow
def test_ai03_masked_text_accepted_by_type_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    status, output = _run_mypy(tmp_path, 'send_to_external(mask("raw text"))\n')
    assert status == 0, output
