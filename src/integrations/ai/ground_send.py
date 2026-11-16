"""AI-Ground 질문 보내기와 전송 확인(결정기록 0026).

실제 화면에서 보내기 버튼(`#chatForm button.send`)이 숨겨져 있어 질문이 입력만 되고 전송되지 않았다.
그래서 질문이 실제로 떠났는지(입력칸이 비워짐·새 답변 등장·주소 변경)를 확인하고,
떠나지 않았으면 다른 보내기 방법을 차례로 시도한다. 모두 실패하면 바로 오류로 끝낸다
(답변 대기 90초를 헛되이 쓰지 않음). 같은 질문을 두 번 보내지 않도록, 앞 방법이 전송된 것으로
확인되면 다음 방법은 시도하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final
from urllib.parse import urlsplit

SEND_CONFIRM_MS: Final = 4000  # 방법마다 전송 확인을 기다리는 최대 시간
SEND_POLL_MS: Final = 250

# 입력칸이 속한 폼을 실제 제출(보내기 버튼을 누른 것과 같은 submit 이벤트)
_REQUEST_SUBMIT_JS: Final = """(sel) => {
  const box = document.querySelector(sel);
  const form = box && box.closest('form');
  if (!form) return false;
  if (typeof form.requestSubmit === 'function') form.requestSubmit(); else form.submit();
  return true;
}"""

# 입력칸 주변(폼 또는 상위 4단계)의 보이는 버튼 구조만(글자 대신 aria-label·title 일부, 값 없음)
_FORM_BUTTONS_JS: Final = """(sel) => {
  const box = document.querySelector(sel);
  if (!box) return [];
  let root = box.closest('form');
  if (!root) {
    root = box;
    for (let i = 0; i < 4 && root.parentElement; i++) root = root.parentElement;
  }
  return Array.from(root.querySelectorAll('button,[role=button],input[type=submit]'))
    .filter(e => e.offsetParent !== null)
    .slice(0, 12)
    .map(e => [e.tagName.toLowerCase(), e.getAttribute('type') || '', e.id ? '#' + e.id : '',
               (typeof e.className === 'string' && e.className.trim())
                 ? '.' + e.className.trim().split(/\\s+/).slice(0, 3).join('.') : '',
               (e.getAttribute('aria-label') || e.getAttribute('title') || '').slice(0, 20),
               e.disabled ? 'disabled' : ''].filter(Boolean).join(' '));
}"""


class SendNotConfirmedError(RuntimeError):
    """보내기 방법을 모두 써도 질문이 전송되지 않았다."""


def _path(page: Any) -> str:
    try:
        return urlsplit(str(page.url)).path
    except Exception:
        return ""


def _sent(
    page: Any, prompt_sel: str, answer_sel: str, answers_before: int, path_before: str
) -> bool:
    try:
        if page.locator(answer_sel).count() > answers_before:
            return True
    except Exception:  # noqa: S110 - 확인 실패는 '아직 안 보냄'으로 보고 다른 신호를 본다
        pass
    if _path(page) != path_before:
        return True  # 새 대화 주소(/c/… 등)로 넘어감
    try:
        return str(page.locator(prompt_sel).first.input_value()).strip() == ""
    except Exception:
        return False


def _confirm(page: Any, check: Callable[[], bool]) -> bool:
    waited = 0
    while waited <= SEND_CONFIRM_MS:
        if check():
            return True
        page.wait_for_timeout(SEND_POLL_MS)
        waited += SEND_POLL_MS
    return check()


def send_and_confirm(page: Any, prompt_sel: str, submit_sel: str, answer_sel: str) -> str:
    """질문을 보내고 전송을 확인한다. 성공한 방법 이름을 돌려준다. 모두 실패하면 예외."""
    answers_before = int(page.locator(answer_sel).count())
    path_before = _path(page)

    def check() -> bool:
        return _sent(page, prompt_sel, answer_sel, answers_before, path_before)

    def click_submit() -> bool:
        button = page.locator(submit_sel).first
        if not (button.is_visible() and button.is_enabled()):
            return False
        button.click()
        return True

    def press_enter() -> bool:
        page.locator(prompt_sel).first.press("Enter")
        return True

    def request_submit() -> bool:
        return bool(page.evaluate(_REQUEST_SUBMIT_JS, prompt_sel))

    methods: list[tuple[str, Callable[[], bool]]] = []
    if submit_sel:
        methods.append(("버튼", click_submit))
    methods += [("Enter", press_enter), ("폼 제출", request_submit)]
    for name, attempt in methods:
        if attempt() and _confirm(page, check):
            return name
    raise SendNotConfirmedError("질문 전송 확인 실패")


def form_buttons(page: Any, prompt_sel: str) -> str:
    """진단용: 입력칸 주변 보이는 버튼 구조."""
    try:
        items = page.evaluate(_FORM_BUTTONS_JS, prompt_sel)
        return "; ".join("".join(ch for ch in str(i)[:80] if ch.isprintable()) for i in list(items))
    except Exception:
        return "?"
