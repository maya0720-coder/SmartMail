"""주간보고 게시 대상·복사 형식 정의(C2, WIKI-05·06, 결정기록 0041).

- 형식 7종: 위키(붙여넣기용 HTML + 위키 마크업), 위키 편집기 원본(저장 형식 XHTML), Slack, Teams,
  메일(Outlook), Markdown, 일반 텍스트.
- 직접 게시는 위키만 한다(사내 시스템, WIKI-03·04). 그 밖의 대상은 승인 후 '복사 + 대상 열기'다.
- Slack·Teams 직접 게시(웹훅·API)는 외부 SaaS 전송이라 이번 범위에서 만들지 않는다(D3).
  `PublishTarget` 인터페이스와 Fake(`integrations/publish/fake.py`)만 두고, 정보보안 승인 뒤
  설정으로 켤 어댑터를 붙인다. 지금은 어디에도 연결하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from domain.weekly.links import Platform


class ExportFormat(StrEnum):
    WIKI = "wiki"
    WIKI_SOURCE = "wiki_source"
    SLACK = "slack"
    TEAMS = "teams"
    MAIL = "mail"
    MARKDOWN = "markdown"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class FormatInfo:
    label: str
    platform: Platform  # 게시 캐시·이력에 남는 대상
    direct: bool  # 직접 게시(위키 API) 여부. 거짓이면 복사 + 열기
    action: str  # 게시 버튼 이름
    has_html: bool  # 클립보드에 text/html도 넣는지


FORMATS: Final[dict[ExportFormat, FormatInfo]] = {
    ExportFormat.WIKI: FormatInfo("위키(Confluence)", Platform.WIKI, True, "위키에 게시", True),
    ExportFormat.WIKI_SOURCE: FormatInfo(
        "위키 편집기용 원본(저장 형식)", Platform.WIKI, False, "복사하고 위키 열기", False
    ),
    ExportFormat.SLACK: FormatInfo("Slack", Platform.SLACK, False, "복사하고 Slack 열기", True),
    ExportFormat.TEAMS: FormatInfo(
        "Microsoft Teams", Platform.TEAMS, False, "복사하고 Teams 열기", True
    ),
    ExportFormat.MAIL: FormatInfo(
        "메일(Outlook)", Platform.MAIL, False, "복사하고 메일 쓰기", True
    ),
    ExportFormat.MARKDOWN: FormatInfo(
        "Markdown(노션·깃 등)", Platform.MARKDOWN, False, "복사하고 링크 열기", False
    ),
    ExportFormat.TEXT: FormatInfo("일반 텍스트", Platform.WEB, False, "복사", False),
}

# 참고 링크 플랫폼의 기본 형식
DEFAULT_FORMAT: Final[dict[Platform, ExportFormat]] = {
    Platform.WIKI: ExportFormat.WIKI,
    Platform.SLACK: ExportFormat.SLACK,
    Platform.TEAMS: ExportFormat.TEAMS,
    Platform.MAIL: ExportFormat.MAIL,
    Platform.MARKDOWN: ExportFormat.MARKDOWN,
    Platform.WEB: ExportFormat.TEXT,
}


class PublishTarget(Protocol):
    """외부 메신저 직접 게시 어댑터 자리(D3: 정보보안 승인 전에는 연결하지 않음).

    입력은 정리·마스킹된 결과만이다(호출하는 쪽이 `export`로 만든 값).
    """

    def post(self, text: str, html: str | None) -> None: ...
