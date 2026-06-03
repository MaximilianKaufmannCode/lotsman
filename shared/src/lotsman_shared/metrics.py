# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Prometheus metrics router factory for Лоцман services.

Exposes GET /metrics with the standard prometheus_client text format.

Standard metrics registered here:
    http_requests_total          - Counter(service, method, path, status_code)
    http_request_duration_seconds - Histogram(service, method, path)
    outbox_pending_total         - Gauge(service, schema) — filled by outbox dispatcher
    arq_jobs_total               - Counter(service, queue, status)

Usage::

    from lotsman_shared.metrics import make_metrics_router, HTTP_REQUESTS_TOTAL
    app.include_router(make_metrics_router())

    # In your route handler (or middleware):
    HTTP_REQUESTS_TOTAL.labels(
        service=settings.service_name, method="GET", path="/api/v1/documents", status_code=200
    ).inc()
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ---------------------------------------------------------------------------
# Standard metric definitions — shared across all Лоцман services
# ---------------------------------------------------------------------------

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests processed",
    ["service", "method", "path", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["service", "method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

OUTBOX_PENDING_TOTAL = Gauge(
    "outbox_pending_total",
    "Number of outbox rows not yet dispatched to Redis Streams",
    ["service", "schema"],
)

ARQ_JOBS_TOTAL = Counter(
    "arq_jobs_total",
    "Total ARQ jobs processed",
    ["service", "queue", "status"],
)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def make_metrics_router() -> APIRouter:
    """Return a FastAPI router exposing GET /metrics in Prometheus text format."""
    router = APIRouter(tags=["metrics"])

    @router.get(
        "/metrics",
        summary="Prometheus metrics",
        response_class=PlainTextResponse,
    )
    async def metrics() -> PlainTextResponse:
        data = generate_latest()
        return PlainTextResponse(
            content=data.decode("utf-8") if isinstance(data, bytes) else data,
            media_type=CONTENT_TYPE_LATEST,
        )

    return router
