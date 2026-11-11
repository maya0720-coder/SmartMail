"""자격증명·세션 잔존 검사(AUTH-07).

앱 시작·종료 시 다음을 점검하고, 발견하면 즉시 지운 뒤 '발견·삭제 건수'만 로그에 남긴다.
- 브라우저 임시 폴더: 앱 전용 임시 폴더(`smartmail-pw-*`, 크로미움 임시 프로필 포함)
- 프로젝트 안의 브라우저 프로필 폴더(`browser-profile*`, `playwright-profile*`, `.playwright`)
- 로그 파일(로그 폴더 바로 아래 *.log*): 쿠키·인증 헤더·암호 대입 형태가 있으면 파일째 삭제
- 추가 검사 문자열(테스트용): 지정한 문자열이 위 경로의 파일에 있으면 삭제
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from security.browser import TEMP_PREFIX

logger = logging.getLogger(__name__)

# 브라우저 프로세스의 TMP를 앱 전용 폴더로 지정하므로 크로미움 임시 프로필도 그 안에 생긴다.
# 다른 프로그램의 임시 폴더를 지우지 않도록 앱 전용 접두사만 대상으로 한다.
TEMP_DIR_PATTERNS: Final = (f"{TEMP_PREFIX}*",)
PROJECT_PROFILE_PATTERNS: Final = ("browser-profile*", "playwright-profile*", ".playwright")

# 로그에 남으면 안 되는 형태(헤더·쿠키·암호 대입). 키워드만이 아니라 값이 붙은 형태를 찾는다.
LOG_RESIDUE: Final = re.compile(
    rb"(?i)(set-cookie\s*:|cookie\s*:\s*\S|authorization\s*:\s*\S|jsessionid=\w"
    rb"|(?:password|passwd|pwd)\s*[=:]\s*\S)"
)
MAX_SCAN_BYTES: Final = 50 * 1024 * 1024
LOG_FILE_PATTERN: Final = "*.log*"


@dataclass(frozen=True, slots=True)
class ResidueReport:
    found: int
    deleted: int


def _remove(path: Path) -> bool:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError:
        return False
    return True


def _file_contains(path: Path, markers: Sequence[bytes], pattern: re.Pattern[bytes] | None) -> bool:
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return False
        data = path.read_bytes()
    except OSError:
        return False
    if pattern is not None and pattern.search(data):
        return True
    return any(marker and marker in data for marker in markers)


def _iter_files(root: Path, pattern: str = "*") -> Iterable[Path]:
    """root 바로 아래의 일반 파일만 돌려준다(하위 폴더·심볼릭 링크·정션은 따라가지 않는다)."""
    if root.is_symlink() or not root.is_dir():
        return
    for path in root.glob(pattern):
        if path.is_file() and not path.is_symlink():
            yield path


class ResidueChecker:
    def __init__(
        self,
        *,
        project_root: Path,
        log_dir: Path,
        temp_root: Path | None = None,
        extra_scan_dirs: Sequence[Path] = (),
    ) -> None:
        self._project_root = project_root
        self._log_dir = log_dir
        self._temp_root = temp_root or Path(tempfile.gettempdir())
        self._extra = tuple(extra_scan_dirs)

    def run(
        self, markers: Sequence[bytes] = (), *, active_temp_dir: Path | None = None
    ) -> ResidueReport:
        """잔존물을 찾아 지운다. markers는 테스트에서 넣은 가짜 자격증명 문자열이다.

        active_temp_dir: 현재 실행 중인 브라우저 임시 폴더(지우지 않음).
        """
        found = deleted = 0
        targets: list[Path] = []
        for pattern in TEMP_DIR_PATTERNS:
            targets.extend(self._temp_root.glob(pattern))
        for pattern in PROJECT_PROFILE_PATTERNS:
            targets.extend(self._project_root.glob(pattern))
        for target in targets:
            if active_temp_dir is not None and target.resolve() == active_temp_dir.resolve():
                continue
            found += 1
            deleted += _remove(target)

        # 로그 폴더는 앱이 만든 로그 파일(*.log, 회전 파일 포함)만 검사한다.
        for path in _iter_files(self._log_dir, LOG_FILE_PATTERN):
            if _file_contains(path, markers, LOG_RESIDUE):
                found += 1
                deleted += _remove(path)
        for root in self._extra:
            for path in _iter_files(root):
                if _file_contains(path, markers, None):
                    found += 1
                    deleted += _remove(path)

        logger.info("잔존 검사: 발견 %d건, 삭제 %d건", found, deleted)
        if deleted < found:
            logger.warning("잔존 검사: 삭제하지 못한 항목 %d건", found - deleted)
        return ResidueReport(found, deleted)
