# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""HTTP gateway for registry-service internal calls.

notification-service calls registry-service using an internal JWT (HS256).
All calls use a shared httpx.AsyncClient with a 10-second timeout per the
iron rules §9.

Implements RegistryDocumentGateway Protocol from application.ports.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog
from lotsman_shared.internal_jwt import issue_internal_jwt

log = structlog.get_logger(__name__)

# System actor UUID used when notification-service makes internal calls.
_SYSTEM_ACTOR = uuid.UUID("00000000-0000-0000-0000-000000000002")
_SYSTEM_ROLE = "admin"


class HttpRegistryDocumentGateway:
    """RegistryDocumentGateway implementation that calls registry-service via HTTP.

    Args:
        base_url: registry-service base URL, e.g. "http://registry-svc:8000".
        signing_key: HS256 key matching registry-service's INTERNAL_JWT_KEY.
        ttl_seconds: JWT TTL (default 60 s).
        client: httpx.AsyncClient (injected; caller owns lifecycle).
    """

    def __init__(
        self,
        base_url: str,
        signing_key: str,
        ttl_seconds: int = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._signing_key = signing_key
        self._ttl = ttl_seconds
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(10.0),
        )

    def _auth_header(self) -> dict[str, str]:
        token = issue_internal_jwt(
            self._signing_key,
            actor_id=_SYSTEM_ACTOR,
            role=_SYSTEM_ROLE,
            audience="registry-service",
            ttl_seconds=self._ttl,
        )
        return {"X-Internal-Token": token}

    async def get_document(self, document_id: uuid.UUID) -> dict[str, Any] | None:
        """GET /api/v1/documents/{id} — returns None on 404.

        registry-service wraps the response as {"document": {...}, "attachments": [...]}.
        We unwrap to return the document dict directly so downstream code can read
        fields like `expiry_date` without nesting.
        """
        url = f"/api/v1/documents/{document_id}"
        try:
            resp = await self._client.get(url, headers=self._auth_header())
        except Exception as exc:
            log.error(
                "registry_gateway.get_document_failed",
                doc_id=str(document_id),
                error=str(exc),
            )
            raise
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and "document" in body and isinstance(body["document"], dict):
            return body["document"]  # type: ignore[no-any-return]
        return body  # type: ignore[no-any-return]

    async def get_document_type(self, type_code: str) -> dict[str, Any] | None:
        """Fetch a single document type by code via the list endpoint + filter.

        registry-service does not expose GET /api/v1/document-types/{code}; only
        the list endpoint is available, so we fetch all and filter client-side.
        """
        url = "/api/v1/document-types"
        try:
            resp = await self._client.get(url, headers=self._auth_header())
        except Exception as exc:
            log.error("registry_gateway.get_doc_type_failed", type_code=type_code, error=str(exc))
            raise
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list):
            return None
        match = next((r for r in rows if r.get("code") == type_code), None)
        return match

    async def list_active_documents(self) -> list[dict[str, Any]]:
        """GET /api/v1/documents?include_archived=false — paginated, returns all."""
        # We do a simple single-page pull; for reconciliation at this scale
        # (up to a few hundred docs) this is acceptable.
        url = "/api/v1/documents"
        params: dict[str, str] = {"include_archived": "false", "limit": "1000"}
        try:
            resp = await self._client.get(url, headers=self._auth_header(), params=params)
        except Exception as exc:
            log.error("registry_gateway.list_documents_failed", error=str(exc))
            raise
        resp.raise_for_status()
        result: list[dict[str, Any]] = resp.json()
        # Filter to only those with expires_at set.
        return [d for d in result if d.get("expires_at") or d.get("expiry_date")]

    async def aclose(self) -> None:
        await self._client.aclose()
