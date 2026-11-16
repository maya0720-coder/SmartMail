"""그룹웨어 로그인 page의 보안문자 지원 메서드(AUTH-01·09, 결정기록 0025).

PlaywrightFormLoginPage가 섞어 쓴다. 이미지·입력값·알림창 글자는 저장·기록하지 않는다.
"""

from __future__ import annotations

from typing import Any, Final

from security.groupware_captcha import MAX_IMAGE_BYTES, ErrorCategory, classify_message

SHOT_TIMEOUT_MS: Final = 5_000
_HIDDEN_JS: Final = """() => Array.from(document.querySelectorAll('form input[type=hidden]'))
  .map(e => e.name || e.id || '(이름 없음)')"""


class CaptchaPageMixin:
    """_page(Playwright Page)를 가진 클래스용. 값은 읽지 않고 개수·이름·상태만 다룬다."""

    _page: Any

    def _init_captcha(self, page: Any) -> None:
        self._captcha_notices = 0
        self._navigations = 0
        self.last_error_category = ErrorCategory.OTHER
        page.on("framenavigated", self._on_navigated)

    def _on_navigated(self, frame: Any) -> None:
        if frame == self._page.main_frame:
            self._navigations += 1

    def _captcha_dialog(self, dialog: Any) -> bool:
        """보안문자 불일치 안내 알림창이면 센 뒤 닫고 True. 글자는 분류에만 쓰고 버린다."""
        category = classify_message(str(dialog.message or ""))
        self.last_error_category = category
        if category is not ErrorCategory.CAPTCHA:
            return False
        self._captcha_notices += 1
        if str(getattr(dialog, "type", "")) == "alert":
            dialog.accept()
        else:
            dialog.dismiss()
        return True

    @property
    def captcha_notice_count(self) -> int:
        return self._captcha_notices

    @property
    def navigation_count(self) -> int:
        return self._navigations

    def capture(self, selector: str) -> bytes | None:
        """같은 page의 이미지 요소를 PNG로 캡처한다(별도 요청으로 다시 받지 않음)."""
        try:
            png = self._page.locator(selector).first.screenshot(type="png", timeout=SHOT_TIMEOUT_MS)
        except Exception:
            return None
        return bytes(png) if len(png) <= MAX_IMAGE_BYTES else None

    def attribute(self, selector: str, name: str) -> str:
        try:
            value = self._page.locator(selector).first.get_attribute(name, timeout=SHOT_TIMEOUT_MS)
        except Exception:
            return ""
        return str(value or "")

    def has_value(self, selector: str) -> bool:
        try:
            return bool(self._page.locator(selector).first.input_value(timeout=SHOT_TIMEOUT_MS))
        except Exception:
            return False

    def hidden_names(self) -> list[str]:
        try:
            return [str(n) for n in self._page.evaluate(_HIDDEN_JS)]
        except Exception:
            return []

    def error_category(self, selector: str) -> ErrorCategory | None:
        """화면 오류 영역이 보이면 글자를 읽어 범주만 돌려준다(글자는 바로 버림)."""
        try:
            node = self._page.locator(selector).first
            if not node.is_visible():
                return None
            text = str(node.inner_text(timeout=SHOT_TIMEOUT_MS))
        except Exception:
            return None
        if not text.strip():
            return None
        self.last_error_category = classify_message(text)
        return self.last_error_category
