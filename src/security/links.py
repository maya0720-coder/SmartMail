"""외부 링크 검증(보안 검토 05~12 M-2).

화면에 href로 내보내거나 DB에 저장하는 외부 링크는 https이고, 허용한 사이트(설정의 기준 주소)와
scheme·호스트·포트가 정확히 같아야 한다. 사용자 정보(userinfo)가 있으면 거부한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urljoin, urlsplit


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 0)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    return parts.scheme, parts.hostname.lower(), port


def safe_link(
    link: str, allowed_bases: Iterable[str], *, base_for_relative: str | None = None
) -> str | None:
    """허용한 사이트의 https 링크면 절대 주소를 돌려주고, 아니면 None."""
    if not link or any(ch in link for ch in "\r\n\t\x00") or len(link) > 2000:
        return None
    absolute = urljoin(base_for_relative, link) if base_for_relative else link
    origin = _origin(absolute)
    if origin is None:
        return None
    allowed = {o for o in (_origin(b) for b in allowed_bases) if o is not None}
    return absolute if origin in allowed else None
