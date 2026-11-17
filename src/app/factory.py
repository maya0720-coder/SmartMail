"""FastAPI 앱 구성."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.container import Container
from app.errors import register_error_handlers
from app.mail_sync import background_sync
from app.routes import (
    auth,
    categories,
    companies,
    data,
    followup,
    gk,
    history,
    jobs,
    login_all,
    mail,
    metrics,
    pages,
    run_mode,
    summaries,
    task_progress,
    tasks,
    title_rule,
    triage,
    triage_bulk,
    ui_prefs,
    weekly,
    weekly_assist,
    weekly_drafts,
    weekly_links,
    weekly_profiles,
    weekly_share,
)
from app.scheduler import PeriodicJob
from app.summary_job import SUMMARY_INTERVAL_SECONDS
from app.templating import STATIC_DIR
from app.web_security import ALLOWED_HOSTS, LOOPBACK_CLIENTS, SecurityMiddleware
from security.logging_policy import EndReason, EventType, Site, log_event

logger = logging.getLogger(__name__)


SESSION_SWEEP_SECONDS = 60
# 자동 새로고침을 끈 경우(0분)에도 시작 시 1회는 수집한다. 다음 실행은 사실상 없음(1년 뒤)
MAIL_SYNC_ONCE_SECONDS = 366 * 24 * 60 * 60


def create_app(
    container: Container,
    *,
    run_background_jobs: bool = True,
    trusted_clients: tuple[str, ...] = LOOPBACK_CLIENTS,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container.residue.run()
        log_event(EventType.APP_START, Site.APP, success=True)
        job = PeriodicJob("retention", container.retention.run)
        sweeper = PeriodicJob("idle", container.sessions.sweep, SESSION_SWEEP_SECONDS)
        # 메일 자동 수집(MAIL-04): 시작 시 1회(처음이면 최근 N일) + 설정 간격마다 증분 수집
        sync_minutes = container.settings.mail_sync_minutes
        mail_job = PeriodicJob(
            "mail-sync",
            lambda: background_sync(container),
            sync_minutes * 60 if sync_minutes > 0 else MAIL_SYNC_ONCE_SECONDS,
        )
        # 업무 제목 AI 요청 요약(결정기록 0014): AI-Ground 로그인 상태에서 2분마다 조금씩
        summary_job = PeriodicJob(
            "summary", container.summary_job.run_once, SUMMARY_INTERVAL_SECONDS
        )
        # 연결 유지 점검(결정기록 0031): 사이트 쪽 만료 감지. 0분이거나 대상이 없으면 끔
        keepalive_minutes = container.settings.session_keepalive_minutes
        keepalive_job = PeriodicJob(
            "keepalive", container.keepalive.run_once, max(keepalive_minutes, 1) * 60
        )
        keepalive_on = keepalive_minutes > 0 and container.keepalive.enabled
        # 외부 회사명 확인(결정기록 0027): 요약 작업과 같은 주기, 수집 흐름과 분리
        company_job = PeriodicJob(
            "company", container.company_job.run_once, SUMMARY_INTERVAL_SECONDS
        )
        # G-Knowledge 등록 큐는 사용자가 누른 작업만 처리하므로 항상 켠다(결정기록 0035)
        container.gk_queue.start()
        if run_background_jobs:
            job.start()
            sweeper.start()
            mail_job.start()
            summary_job.start()
            company_job.start()
            if keepalive_on:
                keepalive_job.start()
        try:
            yield
        finally:
            container.gk_queue.stop()
            await keepalive_job.stop()
            await company_job.stop()
            await summary_job.stop()
            await mail_job.stop()
            await job.stop()
            await sweeper.stop()
            close = getattr(container.mail.collector, "close", None)
            if callable(close):
                close()
            container.sessions.logout_all(EndReason.APP_STOP)
            container.ai.close()
            container.browser.shutdown()
            container.residue.run()
            log_event(EventType.APP_STOP, Site.APP, success=True)

    app = FastAPI(
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.container = container
    container.summary_job.auto = run_background_jobs
    register_error_handlers(app, debug=container.settings.debug)
    app.add_middleware(
        SecurityMiddleware,
        csrf_token=container.csrf_token,
        port=container.settings.app_port,
        trusted_clients=trusted_clients,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(ALLOWED_HOSTS))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(login_all.router)
    app.include_router(auth.router)
    app.include_router(data.router)
    app.include_router(run_mode.router)
    app.include_router(mail.router)
    app.include_router(categories.router)
    app.include_router(companies.router)
    app.include_router(summaries.router)
    app.include_router(triage.router)
    app.include_router(triage.rules_router)
    app.include_router(triage_bulk.router)
    app.include_router(task_progress.router)
    app.include_router(followup.router)
    app.include_router(tasks.router)
    app.include_router(gk.router)
    app.include_router(jobs.router)
    app.include_router(history.router)
    app.include_router(title_rule.router)
    app.include_router(ui_prefs.router)
    app.include_router(weekly.router)
    app.include_router(weekly_drafts.router)
    app.include_router(weekly_links.router)
    app.include_router(weekly_profiles.router)
    app.include_router(weekly_assist.router)
    app.include_router(weekly_share.router)
    app.include_router(metrics.router)
    app.include_router(pages.router)
    return app
