# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Base async HTTP client for web-bff downstream calls.

Every downstream call mints a fresh internal JWT addressed to the target
service (per ADR-0002 §E). The InternalCaller class centralises:
  - JWT minting (issue_internal_jwt)
  - X-Request-Id propagation
  - httpx async client lifecycle
  - Retry on transient errors (tenacity, filled in the bff-resilience feature)

Concrete clients (AuthClient, RegistryClient, etc.) extend this base.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog
from lotsman_shared.internal_jwt import issue_internal_jwt

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(10.0)


class DownstreamClient:
    """Async HTTP client base that automatically mints an internal JWT per call.

    Per ADR-0003 §10: each client receives its target service's dedicated
    ``signing_key`` (INTERNAL_JWT_KEY_<SVC>), NOT a shared secret (F-002).

    Args:
        base_url: The downstream service's base URL (e.g. "http://registry-svc:8000").
        audience: The audience string for the internal JWT (e.g. "registry-service").
        signing_key: Per-target HS256 key (INTERNAL_JWT_KEY_<SVC> env var).
        ttl_seconds: Internal JWT TTL in seconds (default 60).
        timeout: httpx Timeout configuration.
    """

    def __init__(
        self,
        base_url: str,
        audience: str,
        signing_key: str,
        ttl_seconds: int = 60,
        timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url
        self._audience = audience
        self._secret = signing_key
        self._ttl = ttl_seconds
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    def _mint_token(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None,
    ) -> str:
        """Issue a fresh 60-second internal JWT for this downstream service."""
        return issue_internal_jwt(
            self._secret,
            actor_id=actor_id,
            role=role,
            audience=self._audience,
            request_id=request_id,
            ttl_seconds=self._ttl,
        )

    def _headers(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None,
    ) -> dict[str, str]:
        """Build the auth + trace headers for a downstream request."""
        token = self._mint_token(actor_id=actor_id, role=role, request_id=request_id)
        headers: dict[str, str] = {"X-Internal-Token": token}
        if request_id:
            headers["X-Request-Id"] = request_id
        return headers

    async def aclose(self) -> None:
        """Close the underlying httpx client. Called in app lifespan shutdown."""
        await self._client.aclose()

    async def get(
        self,
        path: str,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> httpx.Response:
        headers = self._headers(actor_id=actor_id, role=role, request_id=request_id)
        return await self._client.get(path, headers=headers, params=params)

    async def post(
        self,
        path: str,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        json: Any = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        headers = self._headers(actor_id=actor_id, role=role, request_id=request_id)
        if timeout is not None:
            return await self._client.post(path, headers=headers, json=json, timeout=timeout)
        return await self._client.post(path, headers=headers, json=json)

    async def put(
        self,
        path: str,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        json: Any = None,
    ) -> httpx.Response:
        headers = self._headers(actor_id=actor_id, role=role, request_id=request_id)
        return await self._client.put(path, headers=headers, json=json)

    async def patch(
        self,
        path: str,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        json: Any = None,
    ) -> httpx.Response:
        headers = self._headers(actor_id=actor_id, role=role, request_id=request_id)
        return await self._client.patch(path, headers=headers, json=json)

    async def delete(
        self,
        path: str,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        json: Any = None,
    ) -> httpx.Response:
        headers = self._headers(actor_id=actor_id, role=role, request_id=request_id)
        if json is not None:
            return await self._client.request("DELETE", path, headers=headers, json=json)
        return await self._client.delete(path, headers=headers)
