# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""system-control sidecar application factory.

Entrypoint: uvicorn system_control.main:app --host 0.0.0.0 --port 8000

This sidecar:
  - Listens on the lotsman-internal Docker network ONLY (no host-port mapping in compose).
  - Mounts /var/run/docker.sock for Docker SDK access.
  - Authenticates every /v1/* request via internal JWT (aud="system-control").
  - Provides privileged ops: restart, backup, migrate, logs, ps.

Health / readiness:
  GET /healthz  — liveness (always 200 if process is running)
  GET /readyz   — readiness (checks Docker socket reachability)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from lotsman_shared.health import add_readiness_check, make_health_router
from lotsman_shared.logging import configure_logging
from lotsman_shared.metrics import make_metrics_router
from lotsman_shared.middleware import RequestIdMiddleware

from system_control.api.v1 import router as v1_router
from system_control.config import get_settings

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(service=settings.service_name, level=settings.log_level)
    log.info("system_control_starting", version="0.1.0")

    async def check_docker() -> None:
        """Verify Docker socket is reachable."""
        import docker

        try:
            client = docker.from_env()
            client.ping()
        except Exception as exc:
            raise RuntimeError(f"Docker socket not reachable: {exc}") from exc

    add_readiness_check("docker", check_docker)

    yield

    log.info("system_control_stopping")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Лоцман — system-control",
        version="0.1.0",
        description=(
            "Privileged sidecar for Лоцман: service restart, backup trigger, "
            "Alembic migrate, log proxy, container ps. "
            "Internal network only — no public exposure."
        ),
        lifespan=lifespan,
        # No /api/docs in prod-like config; keep it for dev convenience.
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(RequestIdMiddleware)
    app.include_router(make_health_router())
    app.include_router(make_metrics_router())
    app.include_router(v1_router)

    return app


app = create_app()
