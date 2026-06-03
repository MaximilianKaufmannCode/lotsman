# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""registry-service application factory.

Entrypoint: uvicorn registry_service.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from lotsman_shared.health import add_readiness_check, make_health_router
from lotsman_shared.logging import configure_logging
from lotsman_shared.metrics import make_metrics_router
from lotsman_shared.middleware import RequestIdMiddleware

from registry_service.api.errors import register_exception_handlers
from registry_service.api.internal_files import router as internal_files_router
from registry_service.api.v1 import router as v1_router
from registry_service.config import get_settings
from registry_service.infrastructure.audit_client import AuditServiceHttpClient
from registry_service.infrastructure.db.session import dispose_engine, init_engine

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(service=settings.service_name, level=settings.log_level)
    log.info("registry_service_starting", version="0.2.0")

    engine = init_engine(settings.database_url)
    app.state.settings = settings

    # Wire the signed URL key from settings
    os.environ.setdefault("LOTSMAN_SIGNED_URL_KEY", settings.signed_url_key)

    async def check_postgres() -> None:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))

    add_readiness_check("postgres", check_postgres)

    # Audit client for history queries
    audit_client = AuditServiceHttpClient(
        base_url=settings.audit_svc_url,
        signing_key=settings.internal_jwt_key_audit,
    )
    app.state.audit_client = audit_client

    yield

    log.info("registry_service_stopping")
    await audit_client.aclose()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Лоцман — registry-service",
        version="0.2.0",
        description="Assets, documents, document types, attachments, and xlsx export for Лоцман.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(make_health_router())
    app.include_router(make_metrics_router())
    app.include_router(v1_router, prefix="/api/v1")
    # /internal/files/{path} — signed-URL file serving. Not under /api/v1
    # because the path is fixed in the storage layer's URL generator.
    app.include_router(internal_files_router)

    return app


app = create_app()
