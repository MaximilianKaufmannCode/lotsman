# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Async HTTP client for registry-service.

Implements all registry endpoints needed by the BFF proxy routes.
Internal JWTs are minted per call by the DownstreamClient base class.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from web_bff.infrastructure.clients.base import DownstreamClient


class RegistryClient(DownstreamClient):
    """Client for registry-service endpoints.

    Audience: 'registry-service' (per ADR-0002 §F).
    """

    AUDIENCE = "registry-service"

    # ---------------------------------------------------------------------------
    # Assets
    # ---------------------------------------------------------------------------

    async def list_assets(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> httpx.Response:
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if q:
            params["q"] = q
        return await self.get(
            "/api/v1/assets",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            params=params,
        )

    async def create_asset(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        body: dict[str, Any],
    ) -> httpx.Response:
        return await self.post(
            "/api/v1/assets",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def update_asset(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        asset_id: uuid.UUID,
        body: dict[str, Any],
    ) -> httpx.Response:
        return await self.patch(
            f"/api/v1/assets/{asset_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def archive_asset(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        asset_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.patch(
            f"/api/v1/assets/{asset_id}/archive",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def patch_asset_status(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        asset_id: uuid.UUID,
        status: str,
    ) -> httpx.Response:
        """PATCH /api/v1/assets/{asset_id}/status — change asset lifecycle status.

        status: one of 'active' | 'liquidating' | 'archived'
        """
        return await self.patch(
            f"/api/v1/assets/{asset_id}/status",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"status": status},
        )

    async def get_asset_history(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        asset_id: uuid.UUID,
        limit: int = 50,
    ) -> httpx.Response:
        return await self.get(
            f"/api/v1/assets/{asset_id}/history",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            params={"limit": limit},
        )

    # ---------------------------------------------------------------------------
    # Document types
    # ---------------------------------------------------------------------------

    async def list_document_types(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        return await self.get(
            "/api/v1/document-types",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def create_document_type(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        body: dict[str, Any],
    ) -> httpx.Response:
        return await self.post(
            "/api/v1/document-types",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def update_document_type(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        code: str,
        body: dict[str, Any],
    ) -> httpx.Response:
        return await self.patch(
            f"/api/v1/document-types/{code}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    # ---------------------------------------------------------------------------
    # Documents
    # ---------------------------------------------------------------------------

    async def list_documents(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> httpx.Response:
        return await self.get(
            "/api/v1/documents",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            params=params,
        )

    async def get_document(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        document_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.get(
            f"/api/v1/documents/{document_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def create_document(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        body: dict[str, Any],
    ) -> httpx.Response:
        return await self.post(
            "/api/v1/documents",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def patch_document(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        document_id: uuid.UUID,
        body: dict[str, Any],
    ) -> httpx.Response:
        return await self.patch(
            f"/api/v1/documents/{document_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def archive_document(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        document_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.delete(
            f"/api/v1/documents/{document_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def restore_document(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        document_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.post(
            f"/api/v1/documents/{document_id}/restore",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def bulk_archive_documents(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        body: dict[str, Any],
    ) -> httpx.Response:
        return await self.post(
            "/api/v1/documents/bulk-archive",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def get_document_history(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        document_id: uuid.UUID,
        limit: int = 50,
    ) -> httpx.Response:
        return await self.get(
            f"/api/v1/documents/{document_id}/history",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            params={"limit": limit},
        )

    async def list_distinct_values(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        field: str,
        q: str | None = None,
        limit: int = 100,
    ) -> httpx.Response:
        """GET /api/v1/documents/distinct-values — column-filter autocomplete (v1.24.0)."""
        params: dict[str, Any] = {"field": field, "limit": limit}
        if q is not None:
            params["q"] = q
        return await self.get(
            "/api/v1/documents/distinct-values",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            params=params,
        )

    # ---------------------------------------------------------------------------
    # Attachments
    # ---------------------------------------------------------------------------

    async def upload_attachment(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        document_id: uuid.UUID,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> httpx.Response:
        """Multipart upload — passes through raw file bytes."""
        headers = self._headers(actor_id=actor_id, role=role, request_id=request_id)
        return await self._client.post(
            f"/api/v1/documents/{document_id}/attachments",
            headers=headers,
            files={"file": (filename, data, content_type)},
        )

    async def import_xlsx(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        filename: str,
        data: bytes,
    ) -> httpx.Response:
        """POST /api/v1/imports/xlsx — bulk import .xlsx/.xlsm registry."""
        headers = self._headers(actor_id=actor_id, role=role, request_id=request_id)
        return await self._client.post(
            "/api/v1/imports/xlsx",
            headers=headers,
            files={
                "file": (
                    filename,
                    data,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            timeout=120.0,
        )

    async def list_attachments(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        document_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.get(
            f"/api/v1/documents/{document_id}/attachments",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def download_attachment(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        attachment_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.get(
            f"/api/v1/attachments/{attachment_id}/download",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def delete_attachment(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        attachment_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.delete(
            f"/api/v1/attachments/{attachment_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    # ---------------------------------------------------------------------------
    # Exports
    # ---------------------------------------------------------------------------

    async def request_export(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        body: dict[str, Any],
    ) -> httpx.Response:
        return await self.post(
            "/api/v1/exports",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def get_export_job(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        job_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.get(
            f"/api/v1/exports/{job_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def download_export(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        job_id: uuid.UUID,
    ) -> httpx.Response:
        return await self.get(
            f"/api/v1/exports/{job_id}/download",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    # ---------------------------------------------------------------------------
    # Admin custom-fields (flexible-document-fields)
    # ---------------------------------------------------------------------------

    async def get_custom_field_schema(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        type_code: str,
    ) -> httpx.Response:
        return await self.get(
            f"/api/v1/document-types/admin/{type_code}/custom-fields",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def update_custom_field_schema(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        type_code: str,
        body: dict[str, Any],
    ) -> httpx.Response:
        return await self.put(
            f"/api/v1/document-types/admin/{type_code}/custom-fields",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def import_xlsx_preview(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        filename: str,
        data: bytes,
    ) -> httpx.Response:
        """POST /api/v1/admin/import/preview — multipart xlsx pass-through."""
        headers = self._headers(actor_id=actor_id, role=role, request_id=request_id)
        return await self._client.post(
            "/api/v1/admin/import/preview",
            headers=headers,
            files={
                "file": (
                    filename,
                    data,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            timeout=120.0,
        )

    async def import_xlsx_confirm(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        body: dict[str, Any],
    ) -> httpx.Response:
        """POST /api/v1/admin/import/confirm — JSON decisions."""
        return await self.post(
            "/api/v1/admin/import/confirm",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    # ---------------------------------------------------------------------------
    # Tenant preferences (column order, …)
    # ---------------------------------------------------------------------------

    async def get_column_order(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        return await self.get(
            "/api/v1/preferences/column-order",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def update_column_order(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        order: list[str],
        pinned_column_id: str | None = None,
    ) -> httpx.Response:
        body: dict[str, Any] = {"order": order}
        if pinned_column_id is not None:
            body["pinned_column_id"] = pinned_column_id
        return await self.put(
            "/api/v1/admin/preferences/column-order",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def get_column_labels(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        return await self.get(
            "/api/v1/preferences/column-labels",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def update_column_labels(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        labels: dict[str, str],
    ) -> httpx.Response:
        return await self.put(
            "/api/v1/admin/preferences/column-labels",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"labels": labels},
        )
