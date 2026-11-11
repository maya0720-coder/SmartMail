"""하네스 의존 방향 검사: domain은 수집기·연동·저장소·앱을 import하지 않는다(0-1 규칙 2)."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN_FOR_DOMAIN = ("collectors", "integrations", "storage", "app", "sqlite3", "win32com")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_domain_depends_only_on_interfaces() -> None:
    offenders = {
        str(path.relative_to(SRC)): sorted(_imports(path) & set(FORBIDDEN_FOR_DOMAIN))
        for path in (SRC / "domain").rglob("*.py")
        if _imports(path) & set(FORBIDDEN_FOR_DOMAIN)
    }
    assert offenders == {}


def test_no_file_exceeds_500_lines() -> None:
    long_files = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if len(p.read_text(encoding="utf-8").splitlines()) > 500
    ]
    assert long_files == []
