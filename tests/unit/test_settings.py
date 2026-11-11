"""설정 로딩·검증 테스트(02단계, 개발지침 5장)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from config.settings import (
    ALLOWED_BIND_HOST,
    DEFAULT_RETENTION_DAYS,
    ENV_PREFIX,
    ConfigError,
    load_settings,
)

VALID_ENV = {
    "SMARTMAIL_APP_PORT": "8765",
    "SMARTMAIL_WIKI_BASE_URL": "https://wiki.example.invalid",
    "SMARTMAIL_GKNOWLEDGE_BASE_URL": "https://gk.example.invalid",
    "SMARTMAIL_AIGROUND_BASE_URL": "https://ai.example.invalid",
    "SMARTMAIL_GROUPWARE_MAIL_BASE_URL": "https://gw.example.invalid",
}
HOST = "wiki.example.invalid"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.upper().startswith(ENV_PREFIX):
            monkeypatch.delenv(key)


def _set(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for key, value in {**VALID_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)


def _url_with_userinfo(userinfo: str) -> str:
    """사용자 정보가 들어간 https URL을 만든다(값은 호출 시점에 생성)."""
    return f"https://{userinfo}@{HOST}"


def test_valid_settings_loaded_with_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch)
    settings = load_settings(env_file=None)
    assert settings.app_host == ALLOWED_BIND_HOST
    assert settings.app_port == 8765
    assert settings.debug is False
    assert settings.retention_days == DEFAULT_RETENTION_DAYS
    assert settings.metrics_ledger_keep is False


def test_unprefixed_generic_env_vars_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """다른 도구용 DEBUG=1 같은 일반 변수는 설정에 영향을 주지 않는다(보안 검토 L-1)."""
    _set(monkeypatch)
    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("APP_HOST", "0.0.0.0")  # noqa: S104
    settings = load_settings(env_file=None)
    assert settings.debug is False
    assert settings.app_host == ALLOWED_BIND_HOST


@pytest.mark.parametrize("missing", list(VALID_ENV))
def test_missing_required_value_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _set(monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(ConfigError) as info:
        load_settings(env_file=None)
    assert missing in str(info.value)


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "192.168.0.10", "::"])  # noqa: S104
def test_non_loopback_bind_host_rejected(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    _set(monkeypatch, SMARTMAIL_APP_HOST=host)
    with pytest.raises(ConfigError, match="SMARTMAIL_APP_HOST"):
        load_settings(env_file=None)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SMARTMAIL_APP_PORT", "80"),
        ("SMARTMAIL_APP_PORT", "70000"),
        ("SMARTMAIL_APP_PORT", "abc"),
        ("SMARTMAIL_WIKI_BASE_URL", "<확인 필요>"),
        ("SMARTMAIL_WIKI_BASE_URL", "ftp://wiki.example.invalid"),
        ("SMARTMAIL_GKNOWLEDGE_BASE_URL", "http://gk.example.invalid"),
        ("SMARTMAIL_RETENTION_DAYS", "0"),
        ("SMARTMAIL_HTTP_TIMEOUT_SECONDS", "0"),
    ],
)
def test_invalid_format_rejected(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    _set(monkeypatch, **{key: value})
    with pytest.raises(ConfigError, match=key):
        load_settings(env_file=None)


@pytest.mark.parametrize("kind", ["user_and_secret", "user_only", "query", "fragment"])
def test_auth03_credentials_inside_url_rejected(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """URL 안의 사용자 정보·쿼리 토큰으로 자격증명 금지를 우회하지 못한다(보안 검토 H-3)."""
    generated = secrets.token_hex(6)
    url = {
        "user_and_secret": _url_with_userinfo(":".join(["hong", generated])),
        "user_only": _url_with_userinfo(generated),
        "query": f"https://{HOST}/?api_key={generated}",
        "fragment": f"https://{HOST}/#{generated}",
    }[kind]
    _set(monkeypatch, SMARTMAIL_WIKI_BASE_URL=url)
    with pytest.raises(ConfigError, match="SMARTMAIL_WIKI_BASE_URL") as info:
        load_settings(env_file=None)
    assert generated not in str(info.value)


def test_error_message_does_not_echo_input(monkeypatch: pytest.MonkeyPatch) -> None:
    sensitive_value = "not-a-url-" + secrets.token_hex(8)
    _set(monkeypatch, SMARTMAIL_WIKI_BASE_URL=sensitive_value)
    with pytest.raises(ConfigError) as info:
        load_settings(env_file=None)
    assert sensitive_value not in str(info.value)
    assert info.value.__cause__ is None


def test_auth03_credential_key_in_env_file_rejected(tmp_path: Path) -> None:
    """자격증명 키를 .env에 넣으면 정의되지 않은 키로 시작을 거부한다."""
    generated = secrets.token_hex(8)  # 실행마다 새로 만든 가짜 값
    credential_key = "WIKI_PASSWORD"
    env_file = tmp_path / ".env"
    lines = [f"{k}={v}" for k, v in VALID_ENV.items()] + [f"{credential_key}={generated}"]
    env_file.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ConfigError, match=credential_key) as info:
        load_settings(env_file=env_file, environ={})
    assert generated not in str(info.value)


def test_auth03_credential_key_in_environment_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """SMARTMAIL_* 환경 변수에 미정의 키가 있으면 키 이름만 알리고 거부한다(보안 검토 L-2)."""
    generated = secrets.token_hex(8)
    _set(monkeypatch, SMARTMAIL_WIKI_PASSWORD=generated)
    with pytest.raises(ConfigError, match="SMARTMAIL_WIKI_PASSWORD") as info:
        load_settings(env_file=None)
    assert generated not in str(info.value)


def test_env_file_example_has_no_credential_keys() -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    keys = {
        line.split("=", 1)[0]
        for line in example.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    forbidden = {"PASSWORD", "PASSWD", "SECRET", "TOKEN", "USER_ID", "LOGIN_ID"}
    assert not any(word in key for key in keys for word in forbidden)
    assert all(key.startswith(ENV_PREFIX) for key in keys)
