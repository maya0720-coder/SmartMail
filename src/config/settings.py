"""애플리케이션 설정 로딩·검증.

`.env`(추적 제외, 프로젝트 루트)와 `SMARTMAIL_` 접두사가 붙은 환경 변수에서 설정을 읽는다.
필수값 누락이나 형식 오류는 시작 시 `ConfigError`로 실패시키며, 오류 메시지에는 입력값을
포함하지 않는다.

자격증명(ID·암호·토큰) 설정 키는 두지 않는다(AUTH-03). 정의되지 않은 키가 `.env`나
`SMARTMAIL_*` 환경 변수에 있으면 시작을 거부하고, URL 안의 사용자 정보·쿼리도 거부한다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import Field, HttpUrl, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX: Final = "SMARTMAIL_"
PROJECT_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE: Final = PROJECT_ROOT / ".env"

# 로컬 테스트 버전은 루프백 주소에만 바인딩한다(개발지침 0-1 규칙 7).
ALLOWED_BIND_HOST: Final = "127.0.0.1"

# 메일 참조·완료 업무의 기본 보관 기간(일). 기획서 RET 요구사항.
DEFAULT_RETENTION_DAYS: Final = 7

# 외부 사이트 호출 기본 타임아웃(초).
DEFAULT_HTTP_TIMEOUT_SECONDS: Final = 30.0

# AI-Ground 답변(스트리밍) 대기 기본 시간(초). 화면 자동화라 HTTP보다 길게 둔다(결정기록 0008).
DEFAULT_AI_TIMEOUT_SECONDS: Final = 90.0

# 로컬 DB 기본 위치(추적 제외 폴더).
DEFAULT_DB_PATH: Final = PROJECT_ROOT / "data" / "smartmail.db"
DEFAULT_LOG_DIR: Final = PROJECT_ROOT / "logs"

# 로그인 창을 쓰는 사이트 이름(브라우저 로그인 지정용).
LOGIN_SITE_NAMES: Final = frozenset({"wiki", "g-knowledge", "ai-ground", "groupware"})

Port = Annotated[int, Field(ge=1024, le=65535)]


class ConfigError(RuntimeError):
    """설정 누락·형식 오류. 메시지에는 키 이름과 사유만 담는다."""


class Settings(BaseSettings):
    """애플리케이션 설정. 인스턴스는 불변이다."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        # 빈 값(KEY=)은 미설정으로 보고 기본값을 쓴다.
        env_ignore_empty=True,
    )

    # 서버
    app_host: str = ALLOWED_BIND_HOST
    app_port: Port
    # 오류 응답에 예외 종류 이름을 붙이는 로컬 개발용 플래그. 프레임워크 debug와는 별개다.
    debug: bool = False

    # 외부 사이트 주소(기본값 없음, 확인 필요 항목). https만 허용한다.
    wiki_base_url: HttpUrl
    gknowledge_base_url: HttpUrl
    aiground_base_url: HttpUrl
    groupware_mail_base_url: HttpUrl

    # 보관·지표
    # 기획 기준 7일(RET-01). 운영 조정 여지로 30일까지만 허용한다.
    retention_days: int = Field(default=DEFAULT_RETENTION_DAYS, ge=1, le=30)
    metrics_ledger_keep: bool = False
    # 기한 준수율 목표(0~1). 샘플의 90%는 예시이며 IE팀 확정 전 값(기획서 4.9)
    metrics_due_target: float = Field(default=0.9, ge=0, le=1)
    db_path: Path = DEFAULT_DB_PATH
    # 지표 원장·G-Knowledge R&R의 담당자 표시명(로컬 단일 사용자). 실명 대신 별칭 가능.
    user_display_name: str = Field(default="본인", min_length=1, max_length=50)
    log_dir: Path = DEFAULT_LOG_DIR

    # 로그인·세션(AUTH-05, AUTH-09)
    session_idle_minutes: int = Field(default=30, ge=1, le=480)
    # 2단계 인증·SSO라서 사이트 로그인 화면을 직접 띄울 사이트(쉼표 구분, 확인 후 지정)
    browser_login_sites: str = ""

    # 메일 수집 경로(MAIL-07): auto(감지) / outlook / groupware
    mail_source: Literal["auto", "outlook", "groupware"] = "auto"
    # 제외 규칙 파일(MAIL-05). 비우면 기본 규칙(src/config/rules/mail_exclusions.toml)
    mail_rules_file: Path | None = None
    # 처음 실행 시 자동 수집 기간(일, MAIL-04). 이후에는 마지막 수집 시각부터 증분 수집한다.
    mail_initial_days: int = Field(default=30, ge=1, le=366)
    # 자동 증분 수집 간격(분). 0이면 앱 시작 시 1회만 수집하고 이후는 화면 버튼으로 수집한다.
    mail_sync_minutes: int = Field(default=10, ge=0, le=1440)

    # 지식 AI 어댑터(AI-08): gllm(AI-Ground) / fake(가짜, 외부 전송 없음)
    ai_adapter: Literal["gllm", "fake"] = "gllm"

    # 데모 모드: 모든 외부 연동을 가짜(Fake) 어댑터로 바꾸고 화면에 '데모' 표시
    demo_mode: bool = False

    # 외부 호출 타임아웃(초)
    http_timeout_seconds: float = Field(default=DEFAULT_HTTP_TIMEOUT_SECONDS, gt=0, le=300)
    # AI-Ground 답변 대기 시간(초). 스트리밍 답변이 끝날 때까지 기다리는 상한
    ai_timeout_seconds: float = Field(default=DEFAULT_AI_TIMEOUT_SECONDS, gt=0, le=300)

    @field_validator("app_host")
    @classmethod
    def _only_loopback(cls, value: str) -> str:
        if value != ALLOWED_BIND_HOST:
            raise ValueError(f"바인딩 주소는 {ALLOWED_BIND_HOST}만 허용합니다")
        return value

    @field_validator("browser_login_sites")
    @classmethod
    def _known_sites(cls, value: str) -> str:
        names = {item.strip() for item in value.split(",") if item.strip()}
        if names - LOGIN_SITE_NAMES:
            raise ValueError(
                "브라우저 로그인 사이트는 wiki, g-knowledge, ai-ground, groupware만 허용합니다"
            )
        return ",".join(sorted(names))

    @model_validator(mode="after")
    def _dedicated_log_dir(self) -> Settings:
        # 잔존 검사가 로그 폴더의 파일을 지우므로, 앱 전용 폴더만 허용한다(보안 검토 03·04 M-1).
        log_dir = self.log_dir.resolve()
        forbidden = {PROJECT_ROOT.resolve(), Path.home().resolve(), self.db_path.resolve().parent}
        if log_dir in forbidden or log_dir == Path(log_dir.anchor):
            raise ValueError("로그 폴더는 앱 전용 폴더여야 합니다(프로젝트 루트·홈·DB 폴더 불가)")
        return self

    @property
    def browser_login_site_names(self) -> frozenset[str]:
        return frozenset(item for item in self.browser_login_sites.split(",") if item)

    @field_validator("db_path", "log_dir", "mail_rules_file")
    @classmethod
    def _resolve_db_path(cls, value: Path | None) -> Path | None:
        # 상대 경로는 작업 디렉터리가 아니라 프로젝트 루트 기준으로 해석한다.
        if value is None or value.is_absolute():
            return value
        return PROJECT_ROOT / value

    @field_validator(
        "wiki_base_url", "gknowledge_base_url", "aiground_base_url", "groupware_mail_base_url"
    )
    @classmethod
    def _safe_base_url(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("외부 사이트 주소는 https만 허용합니다")
        if value.username or value.password:
            raise ValueError("주소에 사용자 정보(ID·암호)를 넣을 수 없습니다")
        if value.query or value.fragment:
            raise ValueError("주소에 쿼리·프래그먼트를 넣을 수 없습니다(토큰 노출 방지)")
        return value


_KNOWN_ENV_KEYS: Final = frozenset(ENV_PREFIX + name.upper() for name in Settings.model_fields)


def _reject_unknown_env(environ: Mapping[str, str]) -> None:
    """정의되지 않은 SMARTMAIL_* 환경 변수(예: 자격증명 키)가 있으면 키 이름만 알리고 거부한다."""
    unknown = sorted(
        key
        for key in environ
        if key.upper().startswith(ENV_PREFIX) and key.upper() not in _KNOWN_ENV_KEYS
    )
    if unknown:
        lines = "\n".join(f"- {key.upper()}: 정의되지 않은 설정 키입니다" for key in unknown)
        raise ConfigError("설정 오류로 시작할 수 없습니다.\n" + lines)


def _describe(error: ValidationError) -> str:
    """검증 오류를 입력값 없이 '키: 사유' 목록으로 만든다."""
    lines = []
    for item in error.errors(include_input=False, include_url=False):
        field = ".".join(str(part) for part in item["loc"]) or "(전체)"
        # 정의된 필드는 환경 변수 이름(접두사 포함)으로, 그 외(.env의 미정의 키)는 그대로 표시한다.
        key = ENV_PREFIX + field.upper() if field in Settings.model_fields else field.upper()
        lines.append(f"- {key}: {item['msg']}")
    return "설정 오류로 시작할 수 없습니다.\n" + "\n".join(lines)


def load_settings(
    env_file: Path | None = DEFAULT_ENV_FILE, environ: Mapping[str, str] | None = None
) -> Settings:
    """설정을 읽고 검증한다. 실패하면 `ConfigError`를 던진다.

    env_file이 None이면 환경 변수만 읽는다(테스트용).
    """
    _reject_unknown_env(os.environ if environ is None else environ)
    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigError(_describe(exc)) from None
