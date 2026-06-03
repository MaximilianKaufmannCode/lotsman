# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Domain events for registry-service.

Each event class knows how to produce a canonical EventEnvelope (via as_envelope()).
Topics map to Redis Stream keys per ADR-0002 §C.

Topics:
  registry.documents       — document + attachment events
  registry.assets          — asset events
  registry.document_types  — document type events
  registry.exports         — export job events
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from lotsman_shared.envelope import EventEnvelope, make_envelope

TOPIC_DOCUMENTS = "registry.documents"
TOPIC_ASSETS = "registry.assets"
TOPIC_DOCUMENT_TYPES = "registry.document_types"
TOPIC_EXPORTS = "registry.exports"
TOPIC_IMPORTS = "registry.imports"


# ---------------------------------------------------------------------------
# Asset events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetCreated:
    """Emitted when a new asset is created (US-13)."""

    asset_id: uuid.UUID
    name: str
    inn: str | None
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.asset.created.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "asset_id": str(self.asset_id),
                "name": self.name,
                "inn": self.inn,
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_ASSETS


@dataclass(frozen=True)
class AssetUpdated:
    """Emitted when an asset's field is changed (US-14)."""

    asset_id: uuid.UUID
    field: str
    before: Any
    after: Any
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.asset.updated.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "asset_id": str(self.asset_id),
                "field": self.field,
                "before": self.before,
                "after": self.after,
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_ASSETS


@dataclass(frozen=True)
class AssetStatusChanged:
    """Emitted when an asset's functional status changes (active/liquidating/archived).

    When status is set to 'archived', deleted_at is also set (dual-signal model).
    When status is set to 'active' or 'liquidating', deleted_at may be cleared.
    cascaded_document_count: non-zero only when transitioning to 'archived'.
    """

    asset_id: uuid.UUID
    before: str
    after: str
    cascaded_document_count: int
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.asset.status_changed.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "asset_id": str(self.asset_id),
                "before": self.before,
                "after": self.after,
                "cascaded_document_count": self.cascaded_document_count,
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_ASSETS


@dataclass(frozen=True)
class AssetArchived:
    """Emitted when an asset is soft-deleted (US-15).

    cascaded_document_count: number of active documents that were also archived (Q5).
    """

    asset_id: uuid.UUID
    cascaded_document_count: int
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.asset.archived.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "asset_id": str(self.asset_id),
                "cascaded_document_count": self.cascaded_document_count,
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_ASSETS


# ---------------------------------------------------------------------------
# Document events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentCreated:
    """Emitted when a new document is created (US-5)."""

    document_id: uuid.UUID
    asset_id: uuid.UUID
    type_code: str
    expiry_date: date | None
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.document.created.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "document_id": str(self.document_id),
                "asset_id": str(self.asset_id),
                "type_code": self.type_code,
                "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_DOCUMENTS


@dataclass(frozen=True)
class DocumentUpdated:
    """Emitted when a document field is changed (US-4, US-7, US-9, US-11)."""

    document_id: uuid.UUID
    field: str
    before: Any
    after: Any
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.document.updated.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "document_id": str(self.document_id),
                "field": self.field,
                "before": self.before,
                "after": self.after,
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_DOCUMENTS


@dataclass(frozen=True)
class DocumentArchived:
    """Emitted when a document is soft-deleted (US-6, US-15 cascade)."""

    document_id: uuid.UUID
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.document.archived.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={"document_id": str(self.document_id)},
        )

    @property
    def topic(self) -> str:
        return TOPIC_DOCUMENTS


@dataclass(frozen=True)
class DocumentRestored:
    """Emitted when an archived document is restored (US-7)."""

    document_id: uuid.UUID
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.document.restored.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={"document_id": str(self.document_id)},
        )

    @property
    def topic(self) -> str:
        return TOPIC_DOCUMENTS


@dataclass(frozen=True)
class DocumentBulkArchived:
    """Emitted once for a bulk-archive operation (US-23)."""

    document_ids: list[uuid.UUID]
    count: int
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.document.bulk_archived.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "document_ids": [str(d) for d in self.document_ids],
                "count": self.count,
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_DOCUMENTS


# ---------------------------------------------------------------------------
# DocumentType events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentTypeUpserted:
    """Emitted when a document type is created or updated (US-17)."""

    code: str
    display_name: str
    pre_notice_days: list[int]
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.document_type.upserted.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "code": self.code,
                "display_name": self.display_name,
                "pre_notice_days": self.pre_notice_days,
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_DOCUMENT_TYPES


# ---------------------------------------------------------------------------
# Attachment events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttachmentUploaded:
    """Emitted when an attachment is uploaded to a document (US-9)."""

    attachment_id: uuid.UUID
    document_id: uuid.UUID
    mime_type: str
    size_bytes: int
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.document.updated.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "document_id": str(self.document_id),
                "field": "attachments",
                "before": None,
                "after": {
                    "attachment_id": str(self.attachment_id),
                    "mime_type": self.mime_type,
                    "size_bytes": self.size_bytes,
                },
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_DOCUMENTS


@dataclass(frozen=True)
class AttachmentDeleted:
    """Emitted when an attachment is hard-deleted (US-11)."""

    attachment_id: uuid.UUID
    document_id: uuid.UUID
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.document.updated.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "document_id": str(self.document_id),
                "field": "attachments",
                "before": {"attachment_id": str(self.attachment_id)},
                "after": None,
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_DOCUMENTS


# ---------------------------------------------------------------------------
# ExportJob events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportJobRequested:
    """Emitted when an export job is enqueued (US-20)."""

    job_id: uuid.UUID
    requested_by: uuid.UUID
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.export.requested.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "job_id": str(self.job_id),
                "requested_by": str(self.requested_by),
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_EXPORTS


@dataclass(frozen=True)
class ExportJobCompleted:
    """Emitted when an export job finishes successfully."""

    job_id: uuid.UUID
    file_path: str
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.export.completed.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={"job_id": str(self.job_id), "file_path": self.file_path},
        )

    @property
    def topic(self) -> str:
        return TOPIC_EXPORTS


@dataclass(frozen=True)
class ExportJobFailed:
    """Emitted when an export job fails."""

    job_id: uuid.UUID
    error: str
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.export.failed.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={"job_id": str(self.job_id), "error": self.error},
        )

    @property
    def topic(self) -> str:
        return TOPIC_EXPORTS


@dataclass(frozen=True)
class DocumentTypeFieldsUpdated:
    """Emitted when the custom_field_schema of a document type is changed."""

    type_code: str
    schema_before: list[dict[str, Any]]
    schema_after: list[dict[str, Any]]
    removed_keys: list[str]
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.document_type.fields_updated.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "type_code": self.type_code,
                "schema_before": self.schema_before,
                "schema_after": self.schema_after,
                "removed_keys": self.removed_keys,
                "actor_id": str(self.actor_id),
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_DOCUMENT_TYPES


@dataclass(frozen=True)
class ImportPreviewStarted:
    """Emitted when an import preview session is created."""

    rows_total: int
    unknown_count: int
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.import.preview.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "rows_total": self.rows_total,
                "unknown_count": self.unknown_count,
                "actor_id": str(self.actor_id),
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_IMPORTS


@dataclass(frozen=True)
class ImportCompleted:
    """Emitted when an import confirm finishes."""

    rows_imported: int
    rows_failed: int
    fields_added: list[dict[str, str]]
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.import.completed.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={
                "rows_imported": self.rows_imported,
                "rows_failed": self.rows_failed,
                "fields_added": self.fields_added,
                "actor_id": str(self.actor_id),
            },
        )

    @property
    def topic(self) -> str:
        return TOPIC_IMPORTS


@dataclass(frozen=True)
class ExportJobPurged:
    """Emitted when an expired export job's file is deleted (Q8 cron)."""

    job_id: uuid.UUID
    actor_id: uuid.UUID
    request_id: str | None = None
    occurred_at: datetime | None = None

    def as_envelope(self) -> EventEnvelope:
        return make_envelope(
            event_type="registry.export.purged.v1",
            actor_id=self.actor_id,
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            payload={"job_id": str(self.job_id)},
        )

    @property
    def topic(self) -> str:
        return TOPIC_EXPORTS
