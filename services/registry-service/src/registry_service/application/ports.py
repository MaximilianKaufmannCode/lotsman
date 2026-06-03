# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Repository and service port protocols for registry-service.

Use cases depend on these Protocols ONLY — never on SQLAlchemy or Redis
directly (Iron Rule 2). Infrastructure adapters implement these protocols.

Port types:
  Repositories — persistence operations
  Services     — file I/O, MIME sniffing, XLSX generation, clock
  Bus          — transactional outbox
  HTTP clients — audit-service history queries
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Protocol

from lotsman_shared.envelope import EventEnvelope

from registry_service.domain.entities import (
    Asset,
    Attachment,
    Document,
    DocumentType,
    ExportJob,
)

# ---------------------------------------------------------------------------
# Repository protocols
# ---------------------------------------------------------------------------


class AssetRepository(Protocol):
    """Persistence port for Asset aggregate."""

    async def get_by_id(self, asset_id: uuid.UUID) -> Asset | None: ...

    async def get_active_by_id(self, asset_id: uuid.UUID) -> Asset | None:
        """Returns None if soft-deleted."""
        ...

    async def list_active(
        self,
        *,
        q: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Asset]: ...

    async def add(self, asset: Asset) -> None: ...

    async def update(self, asset: Asset) -> None: ...

    async def name_exists_for_active(self, name: str) -> bool: ...

    async def archive_cascade_documents(
        self,
        asset_id: uuid.UUID,
        now: Any,
    ) -> int:
        """Set deleted_at on all active documents for the asset.

        Returns count of documents that were archived (skips already-archived).
        """
        ...

    async def restore_cascade_documents(
        self,
        asset_id: uuid.UUID,
        now: Any,
    ) -> int:
        """Clear deleted_at on all documents for the asset that were cascade-archived.

        Returns count of documents that were restored.
        Only used when an asset is restored from 'archived' → 'active'/'liquidating'.
        """
        ...


class DocumentRepository(Protocol):
    """Persistence port for Document aggregate."""

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None: ...

    async def list_active(
        self,
        *,
        # legacy single-value params (backward compat)
        asset_id: uuid.UUID | None = None,
        type_code: str | None = None,
        # new multi-value params
        asset_ids: list[uuid.UUID] | None = None,
        type_codes: list[str] | None = None,
        responsible_user_ids: list[uuid.UUID] | None = None,
        responsible_is_null: bool | None = None,
        expiry_from: date | None = None,
        expiry_to: date | None = None,
        expiry_is_null: bool | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        doc_status: list[str] | None = None,
        asset_status: list[str] | None = None,
        inn: str | None = None,
        expiry_dates: list[str] | None = None,
        custom_fields: dict[str, str] | None = None,
        custom_field_ranges: dict[str, dict[str, Any]] | None = None,
        q: str | None = None,
        sort: str | None = None,
        dir: str | None = None,
        offset: int = 0,
        limit: int = 100,
        include_archived: bool = False,
    ) -> list[Document]: ...

    async def add(self, document: Document) -> None: ...

    async def update(self, document: Document) -> None: ...

    async def bulk_archive(
        self,
        document_ids: list[uuid.UUID],
        now: Any,
    ) -> tuple[int, int]:
        """Soft-delete active documents in the given id list.

        Returns:
            (archived_count, skipped_count) — skipped = already archived.
        """
        ...

    async def get_with_asset(self, document_id: uuid.UUID) -> Document | None:
        """Return document joined with asset (for detail view)."""
        ...

    async def count_distinct_values(
        self,
        *,
        field: str,
        q: str | None = None,
        limit: int = 100,
    ) -> tuple[list[tuple[str, int]], int, int]:
        """Return top-N distinct values for a system column with counts + null_count.

        Returns:
            (values, total_distinct, null_count) where:
              - values: list of (value, count) sorted by count DESC then value ASC,
                limited to `limit` rows.
              - total_distinct: count of ALL distinct values (ignoring q/limit).
              - null_count: count of documents where the field is NULL (v1.24.9).
        """
        ...

    async def count_distinct_cf_values(
        self,
        *,
        cf_key: str,
        q: str | None = None,
        limit: int = 100,
    ) -> tuple[list[tuple[str, int]], int, int]:
        """Return top-N distinct values for a custom-field JSONB key with counts.

        cf_key must already be validated against the schema whitelist before calling.
        SQL uses custom_field_values->>'<cf_key>' with GIN @> containment.
        cf_key has been regex-validated before this call (no SQL injection possible).

        Returns same shape as count_distinct_values.
        """
        ...


class DocumentTypeRepository(Protocol):
    """Persistence port for DocumentType catalog."""

    async def get_by_code(self, code: str) -> DocumentType | None: ...

    async def get_by_code_for_update(self, code: str) -> DocumentType | None:
        """Load DocumentType with a SELECT FOR UPDATE lock (serialises concurrent schema edits)."""
        ...

    async def list_all(self) -> list[DocumentType]: ...

    async def upsert(self, doc_type: DocumentType) -> None: ...

    async def drop_custom_field_from_documents(
        self,
        type_code: str,
        field_key: str,
    ) -> None:
        """Remove field_key from all documents.custom_field_values for the given type_code."""
        ...

    async def count_documents_with_field(
        self,
        type_code: str,
        field_key: str,
    ) -> int:
        """Count active documents that hold a value for field_key in JSONB."""
        ...


class AttachmentRepository(Protocol):
    """Persistence port for Attachment records."""

    async def get_by_id(self, attachment_id: uuid.UUID) -> Attachment | None: ...

    async def list_for_document(self, document_id: uuid.UUID) -> list[Attachment]: ...

    async def add(self, attachment: Attachment) -> None: ...

    async def delete(self, attachment_id: uuid.UUID) -> None: ...


class ExportJobRepository(Protocol):
    """Persistence port for ExportJob task records."""

    async def get_by_id(self, job_id: uuid.UUID) -> ExportJob | None: ...

    async def add(self, job: ExportJob) -> None: ...

    async def update(self, job: ExportJob) -> None: ...

    async def list_expired_not_purged(self) -> list[ExportJob]: ...


# ---------------------------------------------------------------------------
# File / MIME service ports
# ---------------------------------------------------------------------------


class AttachmentStorage(Protocol):
    """Port for file I/O on the attachments volume."""

    async def save(
        self,
        *,
        data: bytes,
        document_id: uuid.UUID,
        attachment_id: uuid.UUID,
        original_filename: str,
    ) -> str:
        """Persist bytes to disk. Returns the relative storage_path."""
        ...

    async def delete(self, storage_path: str) -> None:
        """Remove a file from disk. Idempotent — does not raise if already absent."""
        ...

    def signed_url(self, *, storage_path: str, attachment_id: uuid.UUID, ttl_seconds: int) -> str:
        """Return an HMAC-signed URL for serving the file (TTL ≤ 60s per spec)."""
        ...


class ExportStorage(Protocol):
    """Port for saving export .xlsx files."""

    async def save_xlsx(
        self,
        *,
        data: bytes,
        job_id: uuid.UUID,
    ) -> str:
        """Persist xlsx bytes. Returns relative storage_path."""
        ...

    async def delete(self, storage_path: str) -> None: ...

    def signed_url(self, *, storage_path: str, job_id: uuid.UUID, ttl_seconds: int) -> str: ...


class MimeSniffer(Protocol):
    """Port for MIME type detection from file bytes."""

    def sniff(self, data: bytes) -> str:
        """Return the detected MIME type string for the first N bytes of data."""
        ...


class XlsxExporter(Protocol):
    """Port for generating xlsx bytes from a document list."""

    async def export(
        self,
        *,
        documents: list[Document],
        assets: dict[uuid.UUID, Asset],
        visible_columns: list[str],
        snapshot_at: Any,
    ) -> bytes:
        """Render document rows to xlsx bytes."""
        ...


class Clock(Protocol):
    """Provides the current UTC datetime. Tests inject FakeClock."""

    def now(self) -> Any: ...

    def today(self) -> Any: ...


# ---------------------------------------------------------------------------
# Event bus port
# ---------------------------------------------------------------------------


class EventOutbox(Protocol):
    """Port for writing events to the transactional outbox.

    The implementation MUST write in the SAME database session/transaction as
    the business mutation (Iron Rule 6). A call to publish() outside an open
    transaction is a programming error.
    """

    async def publish(self, envelope: EventEnvelope, *, topic: str) -> None: ...


# ---------------------------------------------------------------------------
# Audit service HTTP client port
# ---------------------------------------------------------------------------


class AuditServiceClient(Protocol):
    """Port for querying audit-service history (US-18, US-19)."""

    async def get_events(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        limit: int = 50,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]: ...
