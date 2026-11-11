"""포맷·린트·타입·테스트·시크릿 탐지를 한 번에 실행하는 점검 스크립트.

사용법(저장소 루트에서):
    .venv\\Scripts\\python scripts\\check.py            # 전체 점검
    .venv\\Scripts\\python scripts\\check.py --audit    # 의존성 취약점 검사 포함

의존성 취약점 검사(pip-audit)는 패키지 이름·버전을 공개 취약점 DB에 조회하므로
네트워크가 필요하고, 기본 점검에서는 제외한다.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess  # noqa: S404 - 고정된 인자 배열로만 실행한다.
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# 시크릿 탐지에서 제외할 경로(정규식, Windows 경로 구분자 포함).
# 가상환경·캐시는 외부 패키지라 제외하고, 잠금 파일의 해시는 오탐을 만들어 제외한다.
SCAN_EXCLUDE_PATTERN = (
    r"(^|[\\/])(\.venv|\.git|\.mypy_cache|\.ruff_cache|\.pytest_cache)([\\/]|$)|\.lock$"
)

# src/에서 금지하는 코드 패턴(MaskedText 생성 경로 우회). (설명, 정규식, 허용 파일)
FORBIDDEN_PATTERNS: list[tuple[str, str, str]] = [
    ("마스킹 내부 토큰 직접 사용", r"_CONSTRUCT_TOKEN", "src/security/masking.py"),
    ("MaskedText로 cast", r"cast\(\s*[\"']?MaskedText", ""),
    ("MaskedText 직접 할당 생성", r"__new__\(\s*MaskedText", ""),
]

STEPS: list[tuple[str, list[str]]] = [
    ("포맷", [PY, "-m", "ruff", "format", "--check", "."]),
    ("린트", [PY, "-m", "ruff", "check", "."]),
    ("타입", [PY, "-m", "mypy", "src", "tests", "scripts"]),
    ("테스트", [PY, "-m", "pytest", "-q"]),
]


def run_step(name: str, cmd: list[str]) -> bool:
    """점검 단계 하나를 실행하고 성공 여부를 돌려준다."""
    print(f"\n=== {name} ===", flush=True)
    result = subprocess.run(cmd, cwd=ROOT, check=False)  # noqa: S603
    return result.returncode == 0


def run_secret_scan() -> bool:
    """detect-secrets로 추적 대상 파일을 검사한다. 발견 시 파일·줄 번호만 출력한다."""
    print("\n=== 시크릿 탐지 ===", flush=True)
    cmd = [
        PY,
        "-m",
        "detect_secrets",
        "scan",
        "--all-files",
        "--exclude-files",
        SCAN_EXCLUDE_PATTERN,
    ]
    result = subprocess.run(  # noqa: S603
        cmd, cwd=ROOT, check=False, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        print("시크릿 탐지 도구 실행 실패", file=sys.stderr)
        return False
    findings: dict[str, list[dict[str, object]]] = json.loads(result.stdout)["results"]
    count = sum(len(items) for items in findings.values())
    for path, items in findings.items():
        for item in items:
            # 탐지된 값 자체는 출력하지 않는다.
            print(f"  {path}:{item['line_number']} ({item['type']})")
    print(f"발견 {count}건")
    return count == 0


def run_forbidden_pattern_scan() -> bool:
    """src/에서 마스킹 우회 패턴을 찾는다."""
    print("\n=== 금지 패턴 ===", flush=True)
    count = 0
    for path in sorted((ROOT / "src").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()
        for label, pattern, allowed in FORBIDDEN_PATTERNS:
            if relative == allowed:
                continue
            for number, line in enumerate(lines, start=1):
                if re.search(pattern, line):
                    print(f"  {relative}:{number} ({label})")
                    count += 1
    print(f"발견 {count}건")
    return count == 0


def main() -> int:
    # Windows 콘솔(cp949)에서도 한글 출력이 깨지지 않게 한다.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="저장소 전체 점검")
    parser.add_argument("--audit", action="store_true", help="의존성 취약점 검사 포함")
    args = parser.parse_args()

    results = {name: run_step(name, cmd) for name, cmd in STEPS}
    results["시크릿 탐지"] = run_secret_scan()
    results["금지 패턴"] = run_forbidden_pattern_scan()
    if args.audit:
        results["의존성 취약점"] = run_step(
            "의존성 취약점",
            [PY, "-m", "pip_audit", "--strict", "-r", "requirements-dev.lock"],
        )

    print("\n=== 결과 요약 ===")
    for name, ok in results.items():
        print(f"  {name}: {'통과' if ok else '실패'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
