"""아웃룩 종류 감지와 수집 경로 선택(MAIL-07).

클래식 아웃룩 COM에 연결되면 아웃룩 경로를, 실패하면(새 아웃룩 등) 그룹웨어 경로를 제안한다.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class CollectorKind(StrEnum):
    OUTLOOK = "outlook"
    GROUPWARE = "groupware"


@dataclass(frozen=True, slots=True)
class DetectionResult:
    kind: CollectorKind
    message: str


OUTLOOK_OK = "PC 아웃룩(클래식)에서 메일을 읽습니다"
SWITCH_TO_GROUPWARE = (
    "클래식 아웃룩에 연결할 수 없습니다(새 아웃룩이거나 미설치). 그룹웨어 메일함으로 전환합니다"
)


def detect_collector(probe: Callable[[], None], platform: str = sys.platform) -> DetectionResult:
    """probe(): 클래식 아웃룩 연결을 시험하는 함수. 실패 시 예외를 던진다."""
    if platform != "win32":
        return DetectionResult(CollectorKind.GROUPWARE, SWITCH_TO_GROUPWARE)
    try:
        probe()
    except Exception:  # COM 오류 종류가 다양해 모두 '연결 불가'로 본다
        logger.info("클래식 아웃룩 연결 실패: 그룹웨어 경로 제안")
        return DetectionResult(CollectorKind.GROUPWARE, SWITCH_TO_GROUPWARE)
    return DetectionResult(CollectorKind.OUTLOOK, OUTLOOK_OK)
