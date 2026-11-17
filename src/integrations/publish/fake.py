"""Slack·Teams 직접 게시 Fake(결정기록 0041 D3).

외부 SaaS 전송은 정보보안 승인 전이라 실제 어댑터를 만들지 않았다. 이 Fake는 게시 요청을 메모리에만
담아 두며 네트워크를 쓰지 않는다. 앱 구성(bootstrap)에는 연결하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakePublishTarget:
    """`domain.weekly.targets.PublishTarget` 가짜 구현(테스트·향후 연결 자리)."""

    posts: list[tuple[str, str | None]] = field(default_factory=list)

    def post(self, text: str, html: str | None) -> None:
        self.posts.append((text, html))
