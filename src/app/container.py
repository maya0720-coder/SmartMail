"""앱 구성 요소 묶음. 라우터는 이 컨테이너를 통해서만 서비스에 접근한다."""

from __future__ import annotations

from dataclasses import dataclass

from app.ratelimit import RateLimiter
from collectors.detect import DetectionResult
from config.settings import Settings
from domain.history_service import HistoryService
from domain.knowledge_service import KnowledgeService
from domain.mail_service import MailService
from domain.ports import Clock
from domain.search.pipeline import SearchPipeline
from domain.tasks.service import TaskService
from domain.weekly.service import WeeklyService
from integrations.ai.gateway import AiGateway
from security.browser import BrowserRuntime
from security.credentials import LoginService
from security.residue_check import ResidueChecker
from security.sessions import SessionStore
from storage.db import Database
from storage.knowledge_repos import SqliteLedgerRepository, SqlitePublishLog
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
