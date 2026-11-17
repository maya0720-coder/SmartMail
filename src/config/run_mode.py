"""데이터 모드(실제 데이터 / 가상 데이터) 화면 선택값 저장·적용(결정기록 0042).

- [설정·연결]에서 고른 값을 DB 폴더의 `run_mode.json`에 `{"mode": "real" | "demo"}`로만 저장한다
  (개인정보·자격증명 없음). 앱을 다시 시작할 때 적용한다(DB·연동 구성이 시작 때 정해지므로).
- 우선순위: 프로세스 환경 변수 `SMARTMAIL_DEMO_MODE`(실행 스크립트 `-Demo`) > 화면 선택값 >
  `.env`·기본값. 환경 변수로 정한 실행은 화면 값을 무시하고 화면에 그 사실을 표시한다.
- 파일 형식이 잘못되면 조용히 넘어가지 않고 시작을 거부한다(개발지침 5장).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal

from config.settings import ENV_PREFIX, ConfigError, Settings

RUN_MODE_FILE: Final = "run_mode.json"
DEMO_ENV: Final = ENV_PREFIX + "DEMO_MODE"
MAX_FILE_BYTES: Final = 200
RunMode = Literal["real", "demo"]
MODES: Final[tuple[RunMode, ...]] = ("real", "demo")


def run_mode_path(settings: Settings) -> Path:
    return settings.db_path.parent / RUN_MODE_FILE


def forced_by_env(environ: Mapping[str, str] | None = None) -> bool:
    return DEMO_ENV in (os.environ if environ is None else environ)


def load_saved_mode(path: Path) -> RunMode | None:
    """저장된 화면 선택값. 파일이 없으면 None, 형식이 틀리면 ConfigError."""
    if not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError(
            f"{RUN_MODE_FILE} 파일을 읽을 수 없습니다. 지운 뒤 다시 시작해 주세요"
        ) from None
    mode = data.get("mode") if isinstance(data, dict) and len(data) == 1 else None
    if mode not in MODES:
        raise ConfigError(f"{RUN_MODE_FILE} 형식이 올바르지 않습니다. 지운 뒤 다시 시작해 주세요")
    return "demo" if mode == "demo" else "real"


def save_mode(path: Path, mode: RunMode) -> None:
    """화면 선택값을 저장한다(임시 파일에 쓴 뒤 바꿔 끼워 반쯤 쓴 파일을 남기지 않는다)."""
    if mode not in MODES:
        raise ValueError("unknown mode")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    temp.replace(path)


def apply_run_mode(settings: Settings, environ: Mapping[str, str] | None = None) -> Settings:
    """시작 시 화면 선택값을 설정에 반영한다(환경 변수로 정했으면 그대로 둔다)."""
    if forced_by_env(environ):
        return settings
    saved = load_saved_mode(run_mode_path(settings))
    if saved is None:
        return settings
    return settings.model_copy(update={"demo_mode": saved == "demo"})
