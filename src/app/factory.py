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
from app.routes import auth, data, gk, history, mail, metrics, pages, tasks, weekly
from app.scheduler import PeriodicJob
from app.templating import STATIC_DIR
from app.web_security import ALLOWED_HOSTS, LOOPBACK_CLIENTS, SecurityMiddleware
from security.logging_policy import EventType, Site, log_event

logger = logging.getLogger(__name__)


SESSION_SWEEP_SECONDS = 60


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
        if run_background_jobs:
            job.start()
            sweeper.start()
        try:
            yield
        finally:
            await job.stop()
            await sweeper.stop()
            close = getattr(container.mail.collector, "close", None)
            if callable(close):
                close()
            container.sessions.logout_all()
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
    register_error_handlers(app, debug=container.settings.debug)
    app.add_middleware(
        SecurityMiddleware,
        csrf_token=container.csrf_token,
        port=container.settings.app_port,
        trusted_clients=trusted_clients,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(ALLOWED_HOSTS))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(auth.router)
    app.include_router(data.router)
    app.include_router(mail.router)
    app.include_router(tasks.router)
    app.include_router(gk.router)
    app.include_router(history.router)
    app.include_router(weekly.router)
    app.include_router(metrics.router)
    app.include_router(pages.router)
    return app
