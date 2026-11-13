"""로컬 키 HMAC-SHA256(결정기록 0015, '업무 아님' 학습 키).

- 보낸 사람 주소·제목 말머리 원문 대신 키 해시만 저장하기 위해 쓴다(개인정보 최소화).
- 키 파일은 로컬 데이터 폴더(저장소 작업 트리 안이면 `.gitignore`로 커밋 제외)에 두되,
  Windows DPAPI(현재 사용자 범위)로 감싸서 저장한다. 데이터 폴더가 복사·유출돼도 이 PC의
  같은 Windows 계정이 아니면 키를 풀 수 없다(키와 데이터의 논리적 분리).
- 데모·테스트에서 경로가 없으면 프로세스 메모리에만 둔다.
- 키 파일을 지우면(학습 초기화·전체 삭제) 예전 해시는 다시 계산할 수 없어 의미를 잃는다.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from pathlib import Path
from typing import Final, Protocol

KEY_BYTES: Final = 32
SEPARATOR: Final = "\x1f"
_DESCRIPTION: Final = "SmartMail triage learning"


class KeyProtector(Protocol):
    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, blob: bytes) -> bytes: ...


class DpapiProtector:
    """Windows DPAPI(현재 사용자 범위). pywin32가 없으면 시작 때 실패(안전하지 않은 대체 없음)."""

    def __init__(self) -> None:
        import win32crypt  # type: ignore[import-untyped]  # noqa: PLC0415 - Windows 전용

        self._api = win32crypt

    def protect(self, data: bytes) -> bytes:
        return bytes(self._api.CryptProtectData(data, _DESCRIPTION, None, None, None, 0))

    def unprotect(self, blob: bytes) -> bytes:
        return bytes(self._api.CryptUnprotectData(blob, None, None, None, 0)[1])


class KeyFileError(Exception):
    """학습 키 파일을 읽거나 만들 수 없음(손상·권한·다른 계정)."""


class LocalKeyHasher:
    def __init__(self, key_path: Path | None, protector: KeyProtector | None = None) -> None:
        self._path = key_path
        self._protector = protector
        if key_path is not None and protector is None:
            self._protector = DpapiProtector()
        self._key: bytes | None = None
        self._lock = threading.Lock()

    def digest(self, kind: str, value: str) -> str:
        """kind별로 구분한 HMAC-SHA256 16진 문자열(64자)."""
        message = (kind + SEPARATOR + value).encode("utf-8")
        return hmac.new(self._load(), message, hashlib.sha256).hexdigest()

    def wipe(self) -> None:
        """키를 버린다(파일은 덮어쓴 뒤 삭제). 다음 사용 때 새 키를 만든다."""
        with self._lock:
            self._key = None
            if self._path is None or not self._path.exists():
                return
            try:
                size = self._path.stat().st_size
                with self._path.open("r+b") as handle:
                    handle.write(secrets.token_bytes(size))
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                self._path.unlink(missing_ok=True)

    def _load(self) -> bytes:
        with self._lock:
            if self._key is None:
                self._key = self._read_or_create()
            return self._key

    def _read_or_create(self) -> bytes:
        if self._path is None:
            return secrets.token_bytes(KEY_BYTES)
        assert self._protector is not None  # noqa: S101
        if self._path.exists():
            return self._read()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(KEY_BYTES)
        temp = self._path.with_name(self._path.name + "." + secrets.token_hex(6) + ".tmp")
        try:
            temp.write_bytes(self._protector.protect(key))
            try:
                # 다 쓴 파일만 제자리로 옮긴다(빈 파일이 남지 않음). 이미 있으면 그 키를 쓴다.
                os.link(temp, self._path)
            except FileExistsError:
                return self._read()
        finally:
            temp.unlink(missing_ok=True)
        return key

    def _read(self) -> bytes:
        assert self._path is not None and self._protector is not None  # noqa: S101
        try:
            key = self._protector.unprotect(self._path.read_bytes())
        except Exception as exc:  # DPAPI 오류 형식이 다양하다
            raise KeyFileError("학습 키를 읽을 수 없습니다") from exc
        if len(key) != KEY_BYTES:
            raise KeyFileError("학습 키 파일 형식이 올바르지 않습니다")
        return key
