# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Inbound header sanitiser middleware for web-bff.

Closes F-008 (CWE-348 — user-controlled request headers bypass trust boundary).

A malicious client could send X-Internal-Token (or similar headers) hoping the
BFF passes them through to downstream services. Even though the BFF overwrites
the header when making outbound calls, the presence in inbound logs is undesirable
and creates a confusion surface. Stripping at the earliest ASGI layer is the
safest approach.

Registered as the FIRST middleware in create_app() so even error paths and
exception handlers see clean headers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Headers that must NEVER originate from clients.
# All are lower-cased because HTTP headers are case-insensitive and Starlette
# normalises them to lowercase internally.
_BLOCKED_HEADERS: frozenset[str] = frozenset(
    {
        "x-internal-token",
        "x-forwarded-user",
        "x-forwarded-roles",
        "x-internal-actor",
    }
)


class InboundHeaderSanitiser(BaseHTTPMiddleware):
    """Strip attacker-injectable internal headers from every inbound request.

    This middleware runs before any router, ensuring that:
    - ``X-Internal-Token`` cannot be injected by a client.
    - ``X-Forwarded-User`` / ``X-Forwarded-Roles`` / ``X-Internal-Actor``
      cannot be used to impersonate an authenticated actor.

    Closes F-008.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Rebuild the MutableHeaders copy without the blocked keys.
        headers_to_keep = [
            (name, value)
            for name, value in request.headers.raw
            if name.decode("latin-1").lower() not in _BLOCKED_HEADERS
        ]
        # Replace the request scope's headers with the sanitised list.
        request._headers = None  # type: ignore[assignment]  # force re-parse
        request.scope["headers"] = headers_to_keep

        return await call_next(request)
