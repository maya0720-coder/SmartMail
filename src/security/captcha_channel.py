"""그룹웨어 보안문자 이미지·입력값 전달용 메모리 채널(AUTH-01·09, 결정기록 0025).

- 이미지(PNG)는 로그인 흐름이 넘기고, 화면이 한 번 가져가면(또는 답·취소·시간 초과 시) 바로 버린다.
- 입력값은 로그인 흐름이 꺼내 가는 즉시 지운다. 상태·로그·응답에 담지 않는다.
- ID·암호는 담지 않는다.
"""

from __future__ import annotations

import base64
import threading

from security.groupware_captcha import REFRESH_TOKEN


class CaptchaChannel:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._waiting = False
        self._retry = False
        self._seq = 0  # 새 이미지를 낼 때마다 늘어난다(화면이 새 이미지를 가져올 시점 판단)
        self._image: bytes | None = None
        self._code: str | None = None
        self._cancelled = False

    def snapshot(self) -> dict[str, object]:
        with self._cond:
            return {
                "captcha_waiting": self._waiting,
                "captcha_retry": self._retry,
                "captcha_seq": self._seq,
            }

    def ask(self, png: bytes, retry: bool, timeout: float) -> str | None:
        """이미지를 화면에 내고 입력을 기다린다. 취소·시간 초과면 None."""
        with self._cond:
            self._image, self._retry, self._seq = png, retry, self._seq + 1
            self._code, self._cancelled, self._waiting = None, False, True
            self._cond.wait_for(lambda: self._code is not None or self._cancelled, timeout=timeout)
            code, self._code = self._code, None
            self._image, self._waiting, self._retry = None, False, False
            return code

    def take_image_data_url(self) -> str | None:
        """화면에 보여 줄 data URL(서버가 만든 PNG만). 한 번 가져가면 서버 쪽 이미지는 버린다."""
        with self._cond:
            png, self._image = self._image, None
        if png is None:
            return None
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    def submit(self, code: str) -> bool:
        """화면에서 받은 값을 넘긴다. 입력을 기다리는 중이 아니면 False."""
        with self._cond:
            if not self._waiting:
                return False
            self._code, self._waiting = code, False
            self._cond.notify_all()
            return True

    def refresh(self) -> bool:
        """'새 이미지' 요청. 기다리는 중이 아니면 False."""
        return self.submit(REFRESH_TOKEN)

    def cancel(self) -> None:
        with self._cond:
            self._cancelled, self._waiting, self._retry = True, False, False
            self._code, self._image = None, None
            self._cond.notify_all()
