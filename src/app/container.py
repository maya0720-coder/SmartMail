"""앱 구성 요소 묶음. 라우터는 이 컨테이너를 통해서만 서비스에 접근한다."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from app.ratelimit import RateLimiter
from app.summary_job import SummaryJob
from app.triage_tokens import DecisionTokens
from collectors.detect import DetectionResult
from config.settings import Settings
from domain.history_service import HistoryService
from domain.knowledge_service import KnowledgeService
from domain.mail_service import MailService
from domain.ports import Clock
from domain.search.pipeline import SearchPipeline
from domain.tasks.categories import CategoryCatalog
from domain.tasks.service import TaskService
from domain.weekly.service import WeeklyService
from integrations.ai.gateway import AiGateway
from security.browser import BrowserRuntime
from security.credentials import LoginService
from security.residue_check import ResidueChecker
from security.sessions import SessionStore
from security.site_login import LoginProgress
from storage.db import Database
from storage.knowledge_repos import SqliteLedgerRepository, SqlitePublishLog
from storage.repositories import SqliteAppSettings
from storage.retention import RetentionJob


@dataclass
class Container:
    settings: Settings
    clock: Clock
    db: Database
    sessions: SessionStore
    login: LoginService
    browser: BrowserRuntime
    residue: ResidueChecker
    retention: RetentionJob
    csrf_token: str
    login_limiter: RateLimiter
    mail: MailService
    mail_detection: DetectionResult
    ai: AiGateway
    tasks: TaskService
    knowledge: KnowledgeService
    history: HistoryService
    search: SearchPipeline
    weekly: WeeklyService
    publish_log: SqlitePublishLog
    ledger: SqliteLedgerRepository
    app_settings: SqliteAppSettings
    login_progress: LoginProgress
    catalog: CategoryCatalog
    account_domain: str  # 회사 계정(Entra ID) 도메인: 입력 ID 뒤에 붙인다
    summary_job: SummaryJob  # 업무 제목 AI 요청 요약
    decisions: DecisionTokens  # 제안 결정 취소 토큰(메모리, 60초)
    # 메일 수집 + 업무 반영을 한 번에 하나만 실행(자동 수집과 화면 버튼이 겹치지 않게)
    mail_lock: threading.Lock = field(default_factory=threading.Lock)
    # 시작 로그인 창에서 '나중에'를 눌렀는지(이번 실행 동안만, 메모리)
    login_prompt_dismissed: bool = False
