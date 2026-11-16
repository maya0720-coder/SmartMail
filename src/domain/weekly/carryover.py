"""지난주 주간보고 이월(U5, WIKI-01, 결정기록 0037).

- 지난주 내 보고의 '차주 계획' 항목을 이번 주 업무와 비교한다.
  - 연결된 업무가 완료됨 → '금주 실적' 후보(done)
  - 연결된 업무가 진행 중(진행·완료 후보, 이번 주 메일 오감) → '금주 실적(진행)'(progress)
  - 연결 업무가 없거나 손대지 않음 → '차주 계획 유지' 후보(keep)
- 지난주 내용은 위키 세션이 있으면 위키 페이지의 **내 영역만** 읽고,
  없으면 게시 캐시(마스킹 사본)를 쓴다.
- 비교는 메모리에서만 한다. 지난주 내용과 비교 결과는 저장하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Final, Protocol

from domain.calendar import week_start
from domain.models import Task, TaskStatus
from domain.weekly.form import FormRules, classify_heading
from domain.weekly.rich import blocks, sanitize
from domain.weekly.sections import find_own_section
from domain.weekly.service import WikiApi, WikiUnavailableError
from security.masking import mask

MATCH_RATIO: Final = 0.6  # 제목 유사도 기준(이상이면 같은 업무로 본다)
MAX_CARRY_ITEMS: Final = 50
MAX_ITEM_CHARS: Final = 300
_ITEM_PREFIX = re.compile(r"^(?:[•*\-]|\d{1,2}[.)])\s+")
_WIKI_HEAD = re.compile(r"^h[1-6]\.\s+")
_WIKI_ESCAPE = re.compile(r"\\(.)")
_MODULE = re.compile(r"^\s*\[[^\]]{1,30}\]\s*")
_REQUESTER = re.compile(r"\s*\((?:요청|요청처|요청자)\s*:[^)]*\)\s*$")


@dataclass(frozen=True, slots=True)
class CarryItem:
    text: str
    kind: str  # done | progress | keep
    task_id: int | None = None

    def as_json(self) -> dict[str, Any]:
        return {"text": self.text, "kind": self.kind, "task_id": self.task_id}


class CacheReader(Protocol):
    def list_recent(self, limit: int = 20) -> list[Any]: ...


def _planned(pairs: list[tuple[str, str]], rules: FormRules) -> list[str]:
    """(종류, 글자) 줄 목록에서 '차주 계획' 머리글 아래 항목만 꺼낸다."""
    items: list[str] = []
    current: str | None = None
    for kind, text in pairs:
        if kind == "heading":
            current = classify_heading(text, rules)
        elif current == "planned" and text.strip() not in ("", "-"):
            items.append(mask(" ".join(text.split())).value[:MAX_ITEM_CHARS])
    return items[:MAX_CARRY_ITEMS]


def planned_from_html(section_html: str, rules: FormRules) -> list[str]:
    return _planned(blocks(sanitize(section_html)), rules)


def planned_from_text(snapshot: str, rules: FormRules) -> list[str]:
    """게시 캐시 스냅샷(슬랙·위키 마크업·일반 텍스트)에서 '차주 계획' 항목을 꺼낸다."""
    pairs: list[tuple[str, str]] = []
    for raw in snapshot.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _WIKI_HEAD.match(line):
            pairs.append(("heading", _WIKI_ESCAPE.sub(r"\1", _WIKI_HEAD.sub("", line))))
        elif len(line) > 2 and line.startswith("*") and line.endswith("*") and " " not in line[:2]:
            pairs.append(("heading", line.strip("*")))  # 슬랙 굵게 = 머리글
        elif _ITEM_PREFIX.match(line):
            pairs.append(("item", _WIKI_ESCAPE.sub(r"\1", _ITEM_PREFIX.sub("", line))))
        elif " | " in line:
            pairs.append(("item", line))  # 표 행
        else:
            pairs.append(("heading", line))  # 일반 텍스트 형식의 머리글
    return _planned(pairs, rules)


def core_title(item: str) -> str:
    """항목 형식 '[구분] 제목 (요청: 부서)'에서 제목만."""
    return _REQUESTER.sub("", _MODULE.sub("", item)).strip()


def _score(item: str, title: str) -> float:
    a, b = core_title(item).lower(), title.strip().lower()
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _touched(task: Task, since: datetime) -> bool:
    if task.status in (TaskStatus.IN_PROGRESS, TaskStatus.DONE_CANDIDATE):
        return True
    return task.last_mail_at is not None and task.last_mail_at >= since


def classify_carry(items: list[str], tasks: list[Task], moment: datetime) -> list[CarryItem]:
    since = week_start(moment)
    candidates = [t for t in tasks if t.status not in (TaskStatus.SUGGESTED, TaskStatus.DISMISSED)]
    result: list[CarryItem] = []
    for item in items:
        best: Task | None = None
        best_score = MATCH_RATIO
        for task in candidates:
            score = _score(item, task.title)
            if score >= best_score:
                best, best_score = task, score
        if best is None:
            result.append(CarryItem(item, "keep"))
        elif best.status is TaskStatus.DONE:
            result.append(CarryItem(item, "done", best.id))
        elif _touched(best, since):
            result.append(CarryItem(item, "progress", best.id))
        else:
            result.append(CarryItem(item, "keep", best.id))
    return result


class CarryoverService:
    def __init__(
        self,
        wiki: WikiApi,
        cache: CacheReader,
        tasks: Callable[[], list[Task]],
        page_title: Callable[[datetime], str],
        space_key: Callable[[], str],
        own_heading: Callable[[], str],
        rules: FormRules,
    ) -> None:
        self._wiki = wiki
        self._cache = cache
        self._tasks = tasks
        self._page_title = page_title
        self._space_key = space_key
        self._own_heading = own_heading
        self._rules = rules

    def _from_wiki(self, last_week: datetime) -> list[str] | None:
        space = self._space_key()
        if not space or not self._wiki.has_session():
            return None  # 세션이 없으면 로그인 창을 띄우지 않고 캐시를 쓴다(0034)
        try:
            page = self._wiki.get_page(space, self._page_title(last_week))
        except WikiUnavailableError:
            return None
        if page is None:
            return None
        span = find_own_section(page.body, self._own_heading())
        if span is None:
            return None
        return planned_from_html(page.body[span.start : span.end], self._rules)

    def _from_cache(self, last_week: datetime) -> list[str] | None:
        for entry in self._cache.list_recent():
            if entry.week_start == last_week:
                return planned_from_text(str(entry.snapshot), self._rules)
        return None

    def last_week(self, moment: datetime) -> tuple[str | None, list[CarryItem]]:
        """(출처 wiki|cache|None, 분류 결과). 지난주 보고가 없으면 (None, [])."""
        last_week = week_start(week_start(moment) - timedelta(days=1))
        items = self._from_wiki(last_week)
        source = "wiki" if items is not None else None
        if items is None:
            items = self._from_cache(last_week)
            source = "cache" if items is not None else None
        if items is None:
            return None, []
        return source, classify_carry(items, self._tasks(), moment)
