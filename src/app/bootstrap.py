"""설정으로 컨테이너를 조립한다. 데모 모드에서는 모든 외부 연동을 가짜 어댑터로 바꾼다."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Final

from app.container import Container
from app.ratelimit import RateLimiter
from app.title_rule import apply_saved_rule
from app.web_security import new_csrf_token
from collectors.demo_data import demo_messages
from collectors.detect import CollectorKind, DetectionResult, detect_collector
from collectors.fake import FakeMailCollector
from collectors.groupware import GroupwareMailCollector
from collectors.outlook_com import OutlookComCollector
from config.rules import DEFAULT_MAIL_RULES, load_mail_rules, load_wiki_template
from config.settings import PROJECT_ROOT, Settings
from domain.ai import KnowledgeAI
from domain.clock import SystemClock
from domain.history_service import HistoryService
from domain.knowledge import KnowledgeStore
from domain.knowledge_service import KnowledgeService
from domain.mail import MailCollector
from domain.mail_service import MailService
from domain.ports import Clock
from domain.search.pipeline import SearchPipeline
from domain.tasks.extract import TaskExtractor
from domain.tasks.service import TaskService
from domain.weekly.service import WeeklyService, WikiApi
from integrations.ai.fake import FakeKnowledgeAI
from integrations.ai.gateway import AiGateway
from integrations.ai.ground_browser import BrowserGllmAdapter, load_chat_fields
from integrations.gknowledge.browser import BrowserKnowledgeStore, load_fields
from integrations.gknowledge.fake import FakeKnowledgeStore
from integrations.wiki.confluence import ConfluenceWiki
from integrations.wiki.fake import FakeWiki
from security.browser import BrowserLoginAuthenticator, BrowserRuntime
from security.credentials import (
    LOGIN_SITES,
    FakePasswordAuthenticator,
    InteractiveAuthenticator,
    LoginService,
    PasswordAuthenticator,
)
from security.logging_policy import Site
from security.login_flows import load_login_fields
from security.residue_check import ResidueChecker
from security.sessions import SessionStore
from security.site_login import (
    EntraLoginGroup,
    EntraPasswordAuthenticator,
    LoginProgress,
    WikiPasswordAuthenticator,
)
from storage.db import Database
from storage.knowledge_repos import (
    SqliteAiCallLog,
    SqliteHistoryRepository,
    SqliteLedgerRepository,
    SqlitePublishLog,
)
from storage.repositories import (
    SqliteAppSettings,
    SqliteEventRecorder,
    SqliteMailRefRepository,
    SqliteSyncState,
    SqliteTaskRepository,
)
from storage.retention import RetentionJob

# 사이트별 로그인 시도 제한: 1분에 5회
LOGIN_MAX_ATTEMPTS: Final = 5
LOGIN_WINDOW: Final = timedelta(minutes=1)
# AI 관문 시간 초과 = 어댑터 답변 대기 + 이 여유(초)
AI_GATEWAY_MARGIN_SECONDS: Final = 15.0


def _site_base_url(settings: Settings, site: Site) -> str:
    urls = {
        Site.WIKI: settings.wiki_base_url,
        Site.GKNOWLEDGE: settings.gknowledge_base_url,
        Site.AIGROUND: settings.aiground_base_url,
        Site.GROUPWARE: settings.groupware_mail_base_url,
    }
    return str(urls[site])


DEMO_DETECTION = DetectionResult(CollectorKind.OUTLOOK, "데모 모드: 가상 메일함에서 읽습니다")


def select_collector(
    settings: Settings, sessions: SessionStore, clock: Clock
) -> tuple[MailCollector, DetectionResult]:
    """수집 경로를 고른다(MAIL-07). 데모 모드는 합성 메일을 쓴다."""
    if settings.demo_mode:
        base = clock.now() - timedelta(days=6)
        return FakeMailCollector(demo_messages(base)), DEMO_DETECTION
    groupware = GroupwareMailCollector(sessions)
    if settings.mail_source == "groupware":
        return groupware, DetectionResult(CollectorKind.GROUPWARE, "그룹웨어 메일함에서 읽습니다")
    outlook = OutlookComCollector()
    result = detect_collector(outlook.probe)
    if result.kind is CollectorKind.OUTLOOK or settings.mail_source == "outlook":
        return outlook, result
    outlook.close()
    return groupware, result


def build_container(
    settings: Settings,
    *,
    clock: Clock | None = None,
    db: Database | None = None,
    browser: BrowserRuntime | None = None,
) -> Container:
    clock = clock or SystemClock()
    # 데모 모드는 메모리 DB만 쓴다. 가상 데이터·수집 기준 시각이 실제 DB에 섞이지 않게 한다.
    db = db or (Database.in_memory() if settings.demo_mode else Database(settings.db_path))
    browser = browser or BrowserRuntime()
    sessions = SessionStore(clock, timedelta(minutes=settings.session_idle_minutes))

    password: dict[Site, PasswordAuthenticator] = {}
    interactive: dict[Site, InteractiveAuthenticator] = {}
    login_progress = LoginProgress()
    if settings.demo_mode:
        password = {site: FakePasswordAuthenticator() for site in LOGIN_SITES}
    else:
        # 확인된 사이트는 ID·암호 자동 로그인(결정기록 0011). 그룹웨어는 규격 확인 전(보류).
        fields = load_login_fields()
        entra = EntraLoginGroup(browser, fields, login_progress)
        password[Site.AIGROUND] = EntraPasswordAuthenticator(
            entra, "ai-ground", str(settings.aiground_base_url)
        )
        password[Site.GKNOWLEDGE] = EntraPasswordAuthenticator(
            entra, "g-knowledge", str(settings.gknowledge_base_url)
        )
        password[Site.WIKI] = WikiPasswordAuthenticator(
            browser, str(settings.wiki_base_url), fields.wiki
        )
        # 자동 로그인이 안 될 때 쓰는 대안: 사이트 로그인 화면 직접 열기
        for site in LOGIN_SITES:
            if site.value in settings.browser_login_site_names:
                interactive[site] = BrowserLoginAuthenticator(
                    browser, _site_base_url(settings, site)
                )

    collector, detection = select_collector(settings, sessions, clock)
    events = SqliteEventRecorder(db, clock)
    mail = MailService(
        collector,
        SqliteMailRefRepository(db),
        events,
        load_mail_rules(settings.mail_rules_file or DEFAULT_MAIL_RULES),
        clock,
        sync_state=SqliteSyncState(db),
        initial_sync_days=settings.mail_initial_days,
    )

    ai_adapter: KnowledgeAI
    ai_timeout, ai_retries = settings.http_timeout_seconds, 1
    if settings.demo_mode or settings.ai_adapter == "fake":
        ai_adapter = FakeKnowledgeAI()
    else:
        ai_adapter = BrowserGllmAdapter(
            str(settings.aiground_base_url),
            sessions,
            load_chat_fields(),
            timeout_seconds=settings.ai_timeout_seconds,
        )
        # 화면 자동화는 어댑터가 자체 마감 시각을 가지므로 관문 시간 초과는 그보다 길게 둔다.
        # 보낸 내용이 AI-Ground에 저장·학습되므로 시간 초과 시 다시 보내지 않는다(결정기록 0008).
        ai_timeout, ai_retries = settings.ai_timeout_seconds + AI_GATEWAY_MARGIN_SECONDS, 0
    ai = AiGateway(
        ai_adapter,
        SqliteAiCallLog(db),
        events,
        clock,
        timeout_seconds=ai_timeout,
        retries=ai_retries,
        link_bases=(
            str(settings.aiground_base_url),
            str(settings.gknowledge_base_url),
            str(settings.wiki_base_url),
        ),
    )

    tasks = TaskService(
        SqliteTaskRepository(db),
        SqliteMailRefRepository(db),
        events,
        SqliteLedgerRepository(db),
        clock,
        TaskExtractor(ai),
        settings.user_display_name,
    )

    store: KnowledgeStore
    if settings.demo_mode:
        store = FakeKnowledgeStore()
    else:
        register_fields, search_fields = load_fields()
        store = BrowserKnowledgeStore(
            str(settings.gknowledge_base_url), sessions, register_fields, search_fields
        )
    history = SqliteHistoryRepository(db)
    knowledge = KnowledgeService(store, tasks, history, settings.user_display_name)
    history_service = HistoryService(history, tasks, mail, clock)
    search = SearchPipeline(history_service, knowledge, ai, events, SqliteLedgerRepository(db))

    app_settings = SqliteAppSettings(db)
    template = apply_saved_rule(load_wiki_template(), app_settings)
    wiki: WikiApi
    if settings.demo_mode:
        wiki = FakeWiki()
        if not template.space_key:
            template = replace(template, space_key="DEMO")
    else:
        wiki = ConfluenceWiki(str(settings.wiki_base_url), sessions)
    publish_log = SqlitePublishLog(db)
    author = settings.user_display_name
    if settings.demo_mode and author == "본인":
        author = "데모 사용자"  # 데모 위키는 가짜이므로 고유 이름 요구를 데모 이름으로 충족
    weekly = WeeklyService(tasks, wiki, events, publish_log, clock, template, author)

    return Container(
        settings=settings,
        clock=clock,
        db=db,
        sessions=sessions,
        login=LoginService(sessions, password, interactive),
        browser=browser,
        residue=ResidueChecker(project_root=PROJECT_ROOT, log_dir=settings.log_dir),
        retention=RetentionJob(
            db,
            clock,
            retention_days=settings.retention_days,
            keep_ledger=settings.metrics_ledger_keep,
        ),
        csrf_token=new_csrf_token(),
        login_limiter=RateLimiter(clock, LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW),
        mail=mail,
        mail_detection=detection,
        ai=ai,
        tasks=tasks,
        knowledge=knowledge,
        history=history_service,
        search=search,
        weekly=weekly,
        publish_log=publish_log,
        ledger=SqliteLedgerRepository(db),
        app_settings=app_settings,
        login_progress=login_progress,
    )
