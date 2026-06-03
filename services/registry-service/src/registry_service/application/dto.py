# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Application-layer DTOs for registry-service.

DTOs travel between use cases and the API layer.
They are plain dataclasses — no Pydantic validators, no HTTP knowledge.
Pydantic schemas in api/schemas.py map to/from these.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from datetime import date, datetime
from typing import Any
from typing import Any

# ---------------------------------------------------------------------------
# Asset DTOs
# ---------------------------------------------------------------------------


@dataclass
class CreateAssetCommand:
    name: str
    inn: str | None
    notes: str | None
    actor_id: uuid.UUID
    request_id: str | None = None


@dataclass
class UpdateAssetCommand:
    asset_id: uuid.UUID
    name: str | None
    inn: str | None
    notes: str | None
    actor_id: uuid.UUID
    request_id: str | None = None


@dataclass
class AssetDTO:
    id: uuid.UUID
    name: str
    inn: str | None
    notes: str | None
    status: str  # 'active' | 'liquidating' | 'archived'
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass
class ChangeAssetStatusCommand:
    asset_id: uuid.UUID
    status: str  # 'active' | 'liquidating' | 'archived'
    actor_id: uuid.UUID
    request_id: str | None = None


# ---------------------------------------------------------------------------
# DocumentType DTOs
# ---------------------------------------------------------------------------


@dataclass
class UpsertDocumentTypeCommand:
    code: str
    display_name: str
    pre_notice_days: list[int]
    notify_in_day: bool
    overdue_every_days: int
    actor_id: uuid.UUID
    request_id: str | None = None


@dataclass
class DocumentTypeDTO:
    code: str
    display_name: str
    pre_notice_days: list[int]
    notify_in_day: bool
    overdue_every_days: int
    created_at: datetime
    updated_at: datetime
    # Per-type custom field descriptors (US-2). Serialised as plain dicts so
    # the response shape is compatible with the FE-side CustomField type
    # without pulling the domain class into the DTO layer.
    custom_field_schema: list[dict[str, Any]] = dataclasses_field(default_factory=list)


# ---------------------------------------------------------------------------
# Document DTOs
# ---------------------------------------------------------------------------


@dataclass
class CreateDocumentCommand:
    asset_id: uuid.UUID
    type_code: str
    number: str | None
    issue_date: date | None
    expiry_date: date | None
    responsible_user_id: uuid.UUID | None
    notes: str | None
    actor_id: uuid.UUID
    request_id: str | None = None
    custom_field_values: dict[str, Any] = dataclasses_field(default_factory=dict)


@dataclass
class PatchDocumentCommand:
    document_id: uuid.UUID
    field: str
    value: Any
    actor_id: uuid.UUID
    request_id: str | None = None


@dataclass
class ListDocumentsQuery:
    # --- legacy single-value params (kept for backward compat) ---
    asset_id: uuid.UUID | None = None
    type_code: str | None = None
    # --- new multi-value params ---
    asset_ids: list[uuid.UUID] = dataclasses_field(default_factory=list)
    type_codes: list[str] = dataclasses_field(default_factory=list)
    responsible_user_ids: list[uuid.UUID] = dataclasses_field(default_factory=list)
    responsible_is_null: bool | None = None
    expiry_from: date | None = None
    expiry_to: date | None = None
    expiry_is_null: bool | None = None
    updated_from: datetime | None = None
    updated_to: datetime | None = None
    doc_status: list[str] = dataclasses_field(default_factory=list)
    asset_status: list[str] = dataclasses_field(default_factory=list)
    inn: str | None = None
    # v1.25.6 — column-funnel «— Не задано» for № документа column.
    number_is_null: bool | None = None
    # v1.24.9 — multi-select из воронки колонки «Действ. до».
    # ISO-даты или сентинел __NULL__ (бессрочные).
    expiry_dates: list[str] = dataclasses_field(default_factory=list)
    # custom field containment filters: key -> value (exact match via @> containment)
    # e.g. {"jurisdiction": "RU"} maps to WHERE custom_field_values @> '{"jurisdiction":"RU"}'
    custom_fields: dict[str, str] = dataclasses_field(default_factory=dict)
    # v1.24.17 — schema-driven date-range filters for any custom field of type=date.
    # key -> {"from"?: "YYYY-MM-DD", "to"?: "YYYY-MM-DD", "is_null"?: bool}
    custom_field_ranges: dict[str, dict[str, Any]] = dataclasses_field(default_factory=dict)
    # --- common params ---
    q: str | None = None
    sort: str | None = None
    dir: str | None = None
    # v1.25.5 — urgency status filter. Was `str | None`, now `list[str]`
    # to support multi-select (e.g. одновременно «Истекает» + «Просрочено»
    # for проактивную работу). Single-value URLs `?status=soon` are still
    # accepted by FastAPI's repeated-Query semantics (they parse as ["soon"]).
    status: list[str] = dataclasses_field(default_factory=list)
    offset: int = 0
    limit: int = 100
    include_archived: bool = False


@dataclass
class DocumentDTO:
    id: uuid.UUID
    asset_id: uuid.UUID
    type_code: str
    number: str | None
    issue_date: date | None
    expiry_date: date | None
    responsible_user_id: uuid.UUID | None
    status: str
    urgency_status: str  # computed: ok / soon / overdue / archived
    notes: str | None
    created_by: uuid.UUID
    updated_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    custom_field_values: dict[str, Any] = dataclasses_field(default_factory=dict)


@dataclass
class BulkArchiveCommand:
    ids: list[uuid.UUID]
    actor_id: uuid.UUID
    request_id: str | None = None


@dataclass
class BulkArchiveResult:
    archived: int
    skipped: int


# ---------------------------------------------------------------------------
# Attachment DTOs
# ---------------------------------------------------------------------------


@dataclass
class UploadAttachmentCommand:
    document_id: uuid.UUID
    filename: str
    content_type: str  # declared by client (will be sniffed server-side)
    data: bytes
    actor_id: uuid.UUID
    request_id: str | None = None


@dataclass
class AttachmentDTO:
    id: uuid.UUID
    document_id: uuid.UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_by: uuid.UUID
    created_at: datetime


@dataclass
class SignedUrlDTO:
    url: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# Export DTOs
# ---------------------------------------------------------------------------


@dataclass
class RequestExportCommand:
    filters: dict[str, Any]
    visible_columns: list[str]
    actor_id: uuid.UUID
    request_id: str | None = None


@dataclass
class ExportJobDTO:
    id: uuid.UUID
    requested_by: uuid.UUID
    status: str
    file_path: str | None
    error: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Custom field DTOs (flexible-document-fields)
# ---------------------------------------------------------------------------


@dataclass
class UpdateCustomFieldSchemaCommand:
    type_code: str
    schema: list[Any]  # list of CustomField value objects
    actor_id: uuid.UUID
    request_id: str | None = None


@dataclass
class ImportPreviewCommand:
    actor_id: uuid.UUID
    file_bytes: bytes
    request_id: str | None = None


@dataclass
class ColumnInfo:
    header: str
    matched_to: str  # field key or "custom:<type_code>:<key>" or "unknown"
    suggested_type: str | None = None  # for unknown columns: "text" | "number" | "date"
    samples: list[Any] = dataclasses_field(default_factory=list)


@dataclass
class ImportPreviewDTO:
    import_session_id: str
    rows_total: int
    known_columns: list[ColumnInfo]
    unknown_columns: list[ColumnInfo]


@dataclass
class ImportDecision:
    header: str
    action: str  # "create_new" | "map_to_existing" | "rename" | "skip"
    new_key: str | None = None
    target_type: str | None = None
    field_type: str | None = None
    mapped_to_field: str | None = None
    display_name: str | None = None  # for create_new / rename
    # When true (and action == "create_new"), the new field is added to EVERY
    # document type that actually appears in the imported rows — not only
    # `target_type`. This avoids the silent-drop trap where the user picks
    # one type but the import contains rows of several types.
    apply_to_all_types: bool = False


@dataclass
class ImportConfirmCommand:
    import_session_id: str
    decisions: list[ImportDecision]
    actor_id: uuid.UUID
    request_id: str | None = None


@dataclass
class ImportRowError:
    row_index: int
    error: str


@dataclass
class ImportConfirmDTO:
    rows_imported: int
    rows_failed: int
    fields_added: list[dict[str, str]]  # [{type_code, field_key}]
    errors: list[ImportRowError]


# ---------------------------------------------------------------------------
# Distinct-values DTOs (v1.24.0 — column-filter autocomplete)
# ---------------------------------------------------------------------------


@dataclass
class ListDistinctValuesQuery:
    """Query parameters for the distinct-values endpoint."""

    field: str  # system field name or cf_<key>
    q: str | None = None  # optional substring search within values
    limit: int = 100  # max 500 per spec; default 100


@dataclass
class DistinctValueItem:
    value: str
    count: int


@dataclass
class ListDistinctValuesResult:
    field: str
    values: list[DistinctValueItem]
    total_distinct: int
    truncated: bool  # True when len(values) == limit (more exist)
    # v1.24.6 — for custom-field values, count of documents where the field is
    # missing OR empty. 0 for system fields (where NULL is filtered out earlier).
    null_count: int = 0
