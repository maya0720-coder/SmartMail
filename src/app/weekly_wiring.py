"""주간보고 양식 프로필·지난주 이월·마감 알림 조립(결정기록 0037).

bootstrap.py가 길어지지 않게 주간보고 부가 서비스만 여기서 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from config.rules import load_holidays, load_weekly_form_rules
from config.settings import Settings
from domain.ports import Clock
from domain.tasks.service import TaskService
from domain.weekly.carryover import CarryoverService
from domain.weekly.links import ReferenceLinkService
from domain.weekly.profile_service import WeeklyProfileService
from domain.weekly.remind import WeeklyReminder, parse_remind
from domain.weekly.service import WeeklyService, WikiApi
from storage.db import Database
from storage.knowledge_repos import SqlitePublishLog
from storage.weekly_profile_repo import SqliteWeeklyProfiles
from storage.weekly_repos import SqliteWeeklyCache


@dataclass(frozen=True, slots=True)
class WeeklyExtras:
    profiles: WeeklyProfileService
    carryover: CarryoverService
    reminder: WeeklyReminder


def build_weekly_extras(
    settings: Settings,
    db: Database,
    clock: Clock,
    tasks: TaskService,
    wiki: WikiApi,
    weekly: WeeklyService,
    links: ReferenceLinkService,
    publish_log: SqlitePublishLog,
    cache: SqliteWeeklyCache,
) -> WeeklyExtras:
    rules = load_weekly_form_rules()

    def own_heading() -> str:
        return weekly.own_heading

    def published(week_start: datetime) -> bool:
        """이번 주 게시 기록(위키 게시 이력 또는 게시 캐시)이 있는지."""
        return any(r.week_start == week_start for r in publish_log.list_recent()) or any(
            e.week_start == week_start for e in cache.list_recent()
        )

    return WeeklyExtras(
        profiles=WeeklyProfileService(
            SqliteWeeklyProfiles(db), links, wiki, clock, rules, own_heading
        ),
        carryover=CarryoverService(
            wiki,
            cache,
            lambda: tasks.list_tasks(),
            lambda start: weekly.template.page_title(start),
            lambda: weekly.template.space_key,
            own_heading,
            rules,
        ),
        reminder=WeeklyReminder(
            parse_remind(settings.weekly_remind_at), clock, published, load_holidays()
        ),
    )
