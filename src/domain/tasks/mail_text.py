"""AI에 보낼 메일 본문 발췌 만들기(결정기록 0014·0021). 메모리에서만 쓰고 저장하지 않는다.

- 회신·전달 기록(인용) 앞까지가 기본이다.
- 인용 앞 내용이 짧으면(전달 메일 "아래 건 확인 부탁드립니다" 등) 인용된 원래 메일의 본문을
  '[원래 메일]'로 붙인다. 실제 요청이 인용부에 있기 때문이다.
- 인사말·서명·면책 문구는 뺀다. 줄바꿈은 살린다(번호 목록·표 구조 보존).
- 본문이 HTML이면 태그·스크립트·스타일·엔티티를 먼저 걷어 내
  AI로 보내는 글자를 줄인다(결정기록 0023).
- 호출자는 결과를 반드시 마스킹한 뒤 자른다(AI-02·03).
"""

from __future__ import annotations

import html
import re
from typing import Final

# 회신·전달 기록이 시작되는 줄(그 아래는 이전 메일 인용)
QUOTE_START: Final = re.compile(
    r"^\s*(?:-{2,}\s*(?:Original Message|원본 메시지|원래 메시지|Forwarded message|전달된 메시지)"
    r"|From:|보낸 사람:|보낸사람:|Sent:|On .{0,80} wrote:|>)",
    re.IGNORECASE,
)
# 인용 머리글(보낸 사람·받는 사람·날짜·제목 줄): 원래 메일 본문만 남기려고 뺀다
QUOTE_HEADER: Final = re.compile(
    r"^\s*(?:-{2,}.{0,40}-{2,}\s*$|(?:From|Sent|To|Cc|Date|Subject|보낸 ?사람|받는 ?사람|"
    r"참조|날짜|보낸 날짜|제목)\s*:)",
    re.IGNORECASE,
)
# 여기서부터 아래는 서명·면책 문구로 본다
SIGNATURE_START: Final = re.compile(
    r"^\s*(?:-{2,}\s*$|_{3,}\s*$|감사합니다\.?\s*$|고맙습니다\.?\s*$|.{0,25}\s?드림\.?\s*$|"
    r"best regards|regards,|thanks,|본 메일은|이 메일은 .{0,60}(?:기밀|비밀|법|수신)|"
    r"this (?:e-?mail|message)|confidential|disclaimer|면책)",
    re.IGNORECASE,
)
GREETING: Final = re.compile(
    r"^\s*(?:안녕하세요|안녕하십니까|수고 많으십니다|수고하십니다)[^\n]{0,30}$"
)
MAX_HTML_CHARS: Final = 100_000  # HTML 정리 입력 상한(비정상적으로 큰 본문에서 시간을 쓰지 않게)
# 내용을 통째로 버릴 블록(닫는 태그가 없으면 끝까지)
_HTML_DROP_BLOCK: Final = re.compile(
    r"<(script|style|head|title|template)\b[^<>]*>.*?(?:</\1\s*>|\Z)|<!--.*?(?:-->|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_HTML_BREAK: Final = re.compile(
    r"<\s*(?:br|/p|/div|/tr|/li|/h[1-6]|/table|/ul|/ol|hr|/blockquote)\b[^<>]*>", re.IGNORECASE
)
_HTML_CELL_END: Final = re.compile(r"<\s*/t[dh]\b[^<>]*>", re.IGNORECASE)
_HTML_TAG: Final = re.compile(r"</?[A-Za-z][^<>]*>")
_LOOKS_HTML: Final = re.compile(
    r"</?(?:html|body|div|p|br|span|table|tr|td|style|script|font|a|b|li|ul)\b", re.IGNORECASE
)
FORWARD_HEAD_MIN: Final = 80  # 인용 앞 내용이 이보다 짧으면 원래 메일을 붙인다


def strip_html(text: str) -> str:
    """HTML 본문을 글자만 남긴다(줄 구분은 살림). HTML이 아니면 그대로 돌려준다."""
    if not _LOOKS_HTML.search(text[:MAX_HTML_CHARS]):
        return text
    text = text[:MAX_HTML_CHARS]
    text = _HTML_DROP_BLOCK.sub(" ", text)
    text = _HTML_BREAK.sub("\n", text)
    text = _HTML_CELL_END.sub(" ", text)
    text = _HTML_TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = (re.sub(r"[ \t　]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _clean(lines: list[str]) -> list[str]:
    kept: list[str] = []
    for line in lines:
        if SIGNATURE_START.match(line):
            break
        text = line.strip()
        if not text or GREETING.match(text):
            continue
        kept.append(text)
    return kept


def _original(lines: list[str]) -> list[str]:
    """인용부에서 머리글을 빼고 원래 메일 본문만(다음 인용 전까지)."""
    body: list[str] = []
    started = False
    for line in lines:
        text = line.lstrip("> ").rstrip() if line.lstrip().startswith(">") else line
        if not started:
            if QUOTE_HEADER.match(text) or not text.strip():
                continue
            started = True
        elif QUOTE_HEADER.match(text) and QUOTE_START.match(text):
            break  # 더 이전 메일 인용
        body.append(text)
    return _clean(body)


def mail_excerpt(body: str, limit: int) -> str:
    """요약·해결 방안 요청용 본문 발췌(줄바꿈 유지)."""
    lines = strip_html(body).splitlines()
    cut = next((i for i, line in enumerate(lines) if QUOTE_START.match(line)), len(lines))
    head = _clean(lines[:cut])
    text = "\n".join(head)
    if cut < len(lines) and len(re.sub(r"\s+", "", text)) < FORWARD_HEAD_MIN:
        original = _original(lines[cut:])
        if original:
            text = (text + "\n" if text else "") + "[원래 메일]\n" + "\n".join(original)
    return text[:limit]
