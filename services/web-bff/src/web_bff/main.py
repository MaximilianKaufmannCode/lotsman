# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""web-bff application factory.

Entrypoint: uvicorn web_bff.main:app --host 0.0.0.0 --port 8000

web-bff is the only service that:
  - validates external JWTs from the browser (RS256, the auth feature)
  - mints internal JWTs for downstream calls (HS256, via DownstreamClient)
  - fans out to multiple downstream services in a single request
  - owns CSRF and session-cookie semantics (the auth feature)
  - will serve the React SPA bundle (the web feature)

At scaffold stage it exposes:
  GET /healthz              — own liveness
  GET /readyz               — Redis reachable
  GET /metrics              — Prometheus
  GET /api/v1/system/health — aggregated downstream healthz
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from lotsman_shared.health import add_readiness_check, make_health_router
from lotsman_shared.logging import configure_logging
from lotsman_shared.metrics import make_metrics_router
from lotsman_shared.middleware import RequestIdMiddleware

from web_bff.api.errors import register_exception_handlers
from web_bff.api.v1 import router as v1_router
from web_bff.config import get_settings
from web_bff.infrastructure.clients.audit_client import AuditClient
from web_bff.infrastructure.clients.auth_client import AuthClient
from web_bff.infrastructure.clients.notification_client import NotificationClient
from web_bff.infrastructure.clients.registry_client import RegistryClient
from web_bff.infrastructure.clients.system_control_client import SystemControlClient
from web_bff.infrastructure.middleware.inbound_header_sanitiser import (
    InboundHeaderSanitiser,
)
from web_bff.infrastructure.redis.session_store import SessionStore

log = structlog.get_logger(__name__)


def _assert_unique_internal_jwt_keys(
    key_auth: str,
    key_registry: str,
    key_notification: str,
    key_audit: str,
    key_system_control: str | None = None,
) -> None:
    """Fail fast at startup if any two INTERNAL_JWT_KEY_* values are identical.

    Per ADR-0003 §10 R-5g: reusing values defeats audience isolation (F-002).
    """
    keys: dict[str, str] = {
        "INTERNAL_JWT_KEY_AUTH": key_auth,
        "INTERNAL_JWT_KEY_REGISTRY": key_registry,
        "INTERNAL_JWT_KEY_NOTIFICATION": key_notification,
        "INTERNAL_JWT_KEY_AUDIT": key_audit,
    }
    if key_system_control is not None:
        keys["INTERNAL_JWT_KEY_SYSTEM_CONTROL"] = key_system_control
    seen: dict[str, str] = {}
    for name, value in keys.items():
        for other_name, other_value in seen.items():
            if value == other_value:
                raise ValueError(
                    f"FATAL: {name} and {other_name} have the same value. "
                    "Each INTERNAL_JWT_KEY_* must be unique per service to maintain "
                    "audience isolation (ADR-0003 §10, F-002)."
                )
        seen[name] = value


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(service=settings.service_name, level=settings.log_level)
    log.info("web_bff_starting", version="0.1.0")

    # R-5g: Fail fast if any two per-service JWT keys are equal (F-002)
    _assert_unique_internal_jwt_keys(
        key_auth=settings.internal_jwt_key_auth,
        key_registry=settings.internal_jwt_key_registry,
        key_notification=settings.internal_jwt_key_notification,
        key_audit=settings.internal_jwt_key_audit,
        key_system_control=settings.internal_jwt_key_system_control,
    )

    redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,
    )

    # Wire downstream clients into app.state so deps can retrieve them.
    # Each client uses its target service's dedicated key (ADR-0003 §10 / F-002).
    app.state.auth_client = AuthClient(
        base_url=settings.auth_svc_url,
        audience=AuthClient.AUDIENCE,
        signing_key=settings.internal_jwt_key_auth,
        ttl_seconds=settings.internal_jwt_ttl_seconds,
    )
    app.state.registry_client = RegistryClient(
        base_url=settings.registry_svc_url,
        audience=RegistryClient.AUDIENCE,
        signing_key=settings.internal_jwt_key_registry,
        ttl_seconds=settings.internal_jwt_ttl_seconds,
    )
    app.state.notification_client = NotificationClient(
        base_url=settings.notification_svc_url,
        audience=NotificationClient.AUDIENCE,
        signing_key=settings.internal_jwt_key_notification,
        ttl_seconds=settings.internal_jwt_ttl_seconds,
    )
    app.state.audit_client = AuditClient(
        base_url=settings.audit_svc_url,
        audience=AuditClient.AUDIENCE,
        signing_key=settings.internal_jwt_key_audit,
        ttl_seconds=settings.internal_jwt_ttl_seconds,
    )

    # System-control sidecar (optional — if key not set, sidecar-backed
    # system endpoints return 503 gracefully rather than crashing startup).
    if settings.internal_jwt_key_system_control is not None:
        app.state.system_control_client = SystemControlClient(
            base_url=settings.system_control_url,
            audience=SystemControlClient.AUDIENCE,
            signing_key=settings.internal_jwt_key_system_control,
            ttl_seconds=60,
        )
        log.info("system_control_client_wired", url=settings.system_control_url)
    else:
        app.state.system_control_client = None
        log.warning(
            "system_control_client_not_configured",
            detail="INTERNAL_JWT_KEY_SYSTEM_CONTROL not set; sidecar endpoints will return 503",
        )

    app.state.session_store = SessionStore(redis_client)
    app.state.redis_client = redis_client

    async def check_redis() -> None:
        await redis_client.ping()

    add_readiness_check("redis", check_redis)

    yield

    log.info("web_bff_stopping")
    await app.state.auth_client.aclose()
    await app.state.registry_client.aclose()
    await app.state.notification_client.aclose()
    await app.state.audit_client.aclose()
    if app.state.system_control_client is not None:
        await app.state.system_control_client.aclose()
    await redis_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Лоцман — web-bff",
        version="0.1.0",
        description="Gateway/aggregator for the Лоцман document registry.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # F-008: Strip attacker-injected internal headers FIRST, before any router.
    app.add_middleware(InboundHeaderSanitiser)
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(make_health_router())
    app.include_router(make_metrics_router())
    app.include_router(v1_router, prefix="/api/v1")

    return app


app = create_app()
