"""설정으로 컨테이너를 조립한다. 데모 모드에서는 모든 외부 연동을 가짜 어댑터로 바꾼다."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Final

from app.company_job import CompanyJob
from app.container import Container
from app.demo_seed import seed_demo
from app.gk_job import GkRegisterQueue
from app.ratelimit import RateLimiter
from app.summary_job import SummaryJob
from app.title_rule import apply_saved_rule
from app.triage_tokens import DecisionTokens
from app.web_security import new_csrf_token
from collectors.demo_data import demo_messages
from collectors.demo_scenarios import DOMAIN as DEMO_DOMAIN
from collectors.detect import CollectorKind, DetectionResult, detect_collector
from collectors.fake import FakeMailCollector
from collectors.groupware import GroupwareMailCollector
from collectors.outlook_com import OutlookComCollector
from config.rules import (
    DEFAULT_MAIL_RULES,
    load_category_rules,
    load_company_domains,
    load_mail_rules,
    load_title_rules,
    load_weekly_form_rules,
    load_wiki_template,
)
from config.settings import PROJECT_ROOT, Settings
from domain.ai import KnowledgeAI
from domain.clock import SystemClock
from domain.history_service import HistoryService
from domain.knowledge import KnowledgeStore
from domain.knowledge_service import KnowledgeService
from domain.mail import MailCollector
from domain.mail_service import MailService
from domain.ports import Clock
from domain.search.context import TaskContextBuilder
from domain.search.pipeline import SearchPipeline
from domain.tasks.categories import CategoryCatalog
from domain.tasks.extract import TaskExtractor
from domain.tasks.requester import CompanyResolver, RequesterClassifier
from domain.tasks.service import TaskService
from domain.tasks.summarize import SummaryService
from domain.tasks.triage_rules import TriageLearning
from domain.weekly.drafts import WeeklyDraftStore
from domain.weekly.links import ReferenceLinkService
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
from security.hashing import LocalKeyHasher
from security.keepalive import BrowserMarkerProbe, SessionKeepAlive, SiteProbe
from security.logging_policy import Site
from security.login_flows import load_login_fields
from security.residue_check import ResidueChecker
from security.sessions import SessionStore
from security.site_login import (
    EntraLoginGroup,
    EntraPasswordAuthenticator,
    GroupwarePasswordAuthenticator,
    LoginProgress,
    WikiPasswordAuthenticator,
)
from storage.company_repo import SqliteCompanyDirectory
from storage.db import Database
from storage.gk_state_repo import SqliteGkStates
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
from storage.triage_rules_repo import SqliteTriageRules
from storage.weekly_repos import SqliteReferenceLinks, SqliteWeeklyCache, SqliteWeeklyDrafts

# 사이트별 로그인 시도 제한: 1분에 5회
LOGIN_MAX_ATTEMPTS: Final = 5
LOGIN_WINDOW: Final = timedelta(minutes=1)
# AI 관문 시간 초과 = 어댑터 답변 대기 + 이 여유(초)
AI_GATEWAY_MARGIN_SECONDS: Final = 15.0
LEARNING_KEY_FILE: Final = "triage_hash.bin"  # '업무 아님' 학습 HMAC 키(결정기록 0015)


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
    sessions = SessionStore(
        clock,
        timedelta(minutes=settings.session_idle_minutes),
        keep_while_running=settings.session_keep_while_running,
    )

    password: dict[Site, PasswordAuthenticator] = {}
    interactive: dict[Site, InteractiveAuthenticator] = {}
    login_progress = LoginProgress()
    login_fields = load_login_fields()
    if settings.demo_mode:
        password = {site: FakePasswordAuthenticator() for site in LOGIN_SITES}
    else:
        # 확인된 사이트는 ID·암호 자동 로그인(결정기록 0011). 그룹웨어는 로그인만(결정기록 0017).
        fields = login_fields
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
        # 인증문자 대기(최대 180초) 중에도 AI-Ground 요청이 막히지 않게 별도 브라우저 스레드(0026)
        password[Site.GROUPWARE] = GroupwarePasswordAuthenticator(
            browser.sibling(),
            str(settings.groupware_mail_base_url),
            fields.groupware,
            login_progress,
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

    # 업무 구분(사용자 정의 목록 + 전자결재 판별 규칙, 결정기록 0014)
    app_settings = SqliteAppSettings(db)
    category_rules, title_noise = load_category_rules()
    # '업무 아님' 학습 키: DB와 같은 로컬 폴더. 데모(메모리 DB)는 파일 없이 메모리에만
    learning_key = None if settings.demo_mode else settings.db_path.parent / LEARNING_KEY_FILE
    catalog = CategoryCatalog(app_settings, category_rules)
    # 요청자 내부·외부 구분과 외부 회사명(결정기록 0027). 내부 도메인 설정이 없으면 회사 계정 도메인
    company_rules, free_mail = load_company_domains()
    internal = settings.internal_domain_names or (login_fields.entra.account_domain,)
    if settings.demo_mode:
        internal = (*internal, DEMO_DOMAIN)  # 가상 메일 도메인은 내부로 본다(가상 부서명 유지)
    requesters = RequesterClassifier(internal, free_mail)
    company_cache = SqliteCompanyDirectory(db, clock)
    companies = CompanyResolver(company_rules, company_cache)
    tasks = TaskService(
        SqliteTaskRepository(db),
        SqliteMailRefRepository(db),
        events,
        SqliteLedgerRepository(db),
        clock,
        TaskExtractor(ai, categorizer=catalog, noise_prefixes=title_noise),
        settings.user_display_name,
        catalog=catalog,
        learning=TriageLearning(SqliteTriageRules(db, clock), LocalKeyHasher(learning_key)),
        requesters=requesters,
    )
    # 예전 SAP 모듈 태그처럼 목록에 없는 구분은 시작할 때 규칙으로 다시 구분한다
    tasks.recategorize_unknown()

    store: KnowledgeStore
    if settings.demo_mode:
        store = FakeKnowledgeStore()
    else:
        register_fields, search_fields = load_fields()
        store = BrowserKnowledgeStore(
            str(settings.gknowledge_base_url), sessions, register_fields, search_fields
        )
    history = SqliteHistoryRepository(db)
    knowledge = KnowledgeService(
        store,
        tasks,
        history,
        settings.user_display_name,
        home_link=str(settings.gknowledge_base_url),
    )
    history_service = HistoryService(history, tasks, mail, clock)
    if settings.demo_mode and settings.demo_seed:
        seed_demo(tasks, history_service, store)  # 가상 과거 사례·문서(결정기록 0021)
    # 업무 제목 = AI 요청 요약(결정기록 0014). 가짜 AI(데모·fake 설정)는 로그인 없이 동작
    summary_job = SummaryJob(
        SummaryService(
            tasks,
            SqliteMailRefRepository(db),
            mail,
            ai,
            load_title_rules(),
            concurrency=settings.summary_concurrency,
        ),
        sessions,
        always_ready=isinstance(ai_adapter, FakeKnowledgeAI),
    )
    # 연결 유지 점검(결정기록 0031): 표식이 확인된 회사 계정 사이트만. 데모는 점검하지 않음
    probes: dict[Site, SiteProbe] = {}
    if not settings.demo_mode and settings.session_keepalive_minutes > 0:
        for site, url in (
            (Site.AIGROUND, settings.aiground_base_url),
            (Site.GKNOWLEDGE, settings.gknowledge_base_url),
        ):
            marker = login_fields.site_logged_in.get(site.value)
            if marker:
                probes[site] = BrowserMarkerProbe(str(url), marker)
    keepalive = SessionKeepAlive(sessions, probes, busy=lambda: summary_job.state.running)
    # 해결 방안 요청에 업무 맥락(구조화 요약·메일 발췌, 마스킹)을 붙인다(결정기록 0021)
    task_context = TaskContextBuilder(tasks, SqliteMailRefRepository(db), mail)
    search = SearchPipeline(
        history_service,
        knowledge,
        ai,
        events,
        SqliteLedgerRepository(db),
        context=task_context.build,
    )

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
    weekly_cache = SqliteWeeklyCache(db)
    weekly = WeeklyService(
        tasks,
        wiki,
        events,
        publish_log,
        clock,
        template,
        author,
        cache=weekly_cache,
        form_rules=load_weekly_form_rules(),
        wiki_base_url=str(settings.wiki_base_url),
        link_bases=(str(settings.gknowledge_base_url),),
    )

    # 로그인 없이 동작하는 사이트(결정기록 0034)
    # 가짜 AI, 데모 G-Knowledge·위키, 그룹웨어가 아닌 메일 수집 경로
    login_free: set[Site] = set()
    if isinstance(ai_adapter, FakeKnowledgeAI):
        login_free.add(Site.AIGROUND)
    if settings.demo_mode:
        login_free.update((Site.GKNOWLEDGE, Site.WIKI))
    if detection.kind is not CollectorKind.GROUPWARE:
        login_free.add(Site.GROUPWARE)

    return Container(
        login_free_sites=frozenset(login_free),
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
        catalog=catalog,
        account_domain=login_fields.entra.account_domain,
        summary_job=summary_job,
        keepalive=keepalive,
        companies=companies,
        company_cache=company_cache,
        company_job=CompanyJob(
            tasks,
            companies,
            company_cache,
            ai,
            sessions,
            clock,
            always_ready=isinstance(ai_adapter, FakeKnowledgeAI),
        ),
        decisions=DecisionTokens(clock),
        weekly_links=ReferenceLinkService(SqliteReferenceLinks(db), str(settings.wiki_base_url)),
        weekly_cache=weekly_cache,
        weekly_drafts=WeeklyDraftStore(
            SqliteWeeklyDrafts(db),
            clock,
            (str(settings.wiki_base_url), str(settings.gknowledge_base_url)),
        ),
        gk_queue=GkRegisterQueue(knowledge, SqliteGkStates(db), clock),
    )
