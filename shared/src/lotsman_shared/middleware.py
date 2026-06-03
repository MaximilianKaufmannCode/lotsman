# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ASGI middleware for Лоцман services.

RequestIdMiddleware:
    - Reads the X-Request-Id header from inbound requests.
    - Generates a UUIDv4 if the header is absent.
    - Binds the value to structlog contextvars so all log lines in the
      request scope include request_id automatically.
    - Forwards the same id in the X-Request-Id response header.

Usage::

    from lotsman_shared.middleware import RequestIdMiddleware
    app.add_middleware(RequestIdMiddleware)
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request-id to every request and bind it to structlog context."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())

        # Bind to structlog for the lifetime of this request coroutine.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response
