# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Pydantic request/response schemas for registry-service API.

These are the HTTP boundary types. They map to/from DTOs in application/dto.py.
All user-visible text is in Russian per the project conventions.
Dates in API payloads use ISO-8601 per spec §6.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# ---------------------------------------------------------------------------
# Asset schemas
# ---------------------------------------------------------------------------


class AssetCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    inn: str | None = Field(default=None, min_length=10, max_length=12)
    notes: str | None = None

    @field_validator("inn")
    @classmethod
    def inn_digits_only(cls, v: str | None) -> str | None:
        if v is not None and not v.isdigit():
            raise ValueError("INN must contain digits only")
        return v


class AssetUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    inn: str | None = Field(default=None, min_length=10, max_length=12)
    notes: str | None = None

    @field_validator("inn")
    @classmethod
    def inn_digits_only(cls, v: str | None) -> str | None:
        if v is not None and not v.isdigit():
            raise ValueError("INN must contain digits only")
        return v


class AssetResponse(BaseModel):
    id: uuid.UUID
    name: str
    inn: str | None
    notes: str | None
    status: str  # 'active' | 'liquidating' | 'archived'
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class AssetStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(active|liquidating|archived)$")


# ---------------------------------------------------------------------------
# DocumentType schemas
# ---------------------------------------------------------------------------


class DocumentTypeUpsertRequest(BaseModel):
    code: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,63}$")
    display_name: str = Field(..., min_length=1, max_length=200)
    pre_notice_days: list[int] = Field(..., min_length=1)
    notify_in_day: bool = True
    overdue_every_days: int = Field(..., ge=1)

    @field_validator("pre_notice_days")
    @classmethod
    def all_positive(cls, v: list[int]) -> list[int]:
        if any(d <= 0 for d in v):
            raise ValueError("All values must be positive integers")
        return v


class DocumentTypeResponse(BaseModel):
    code: str
    display_name: str
    pre_notice_days: list[int]
    notify_in_day: bool
    overdue_every_days: int
    created_at: datetime
    updated_at: datetime
    # Per-type custom field descriptors. Empty list when none configured.
    custom_field_schema: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Document schemas
# ---------------------------------------------------------------------------


class DocumentCreateRequest(BaseModel):
    asset_id: uuid.UUID
    type_code: str = Field(..., min_length=1, max_length=64)
    number: str | None = Field(default=None, max_length=500)
    issue_date: date | None = None
    expiry_date: date | None = None
    responsible_user_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    custom_field_values: dict[str, Any] = Field(default_factory=dict)


class DocumentPatchRequest(BaseModel):
    """Partial-object patch for one or more document fields.

    v1.25.0: extended to full edit scope (US-4 + new):
      asset_id, type_code, number, issue_date, expiry_date, responsible_user_id,
      notes, custom_field_values.

    Handler iterates ``model_fields_set`` and invokes InlineEditDocument once
    per field so audit granularity (one DocumentUpdated event per field) is
    preserved. Iteration order is sorted so that type_code is applied BEFORE
    custom_field_values — when type changes, orphan cf-keys are dropped
    against the new type's schema before user-supplied cf values are validated.

    Whitelist of patchable fields lives in
    ``application/use_cases/inline_edit_document._PATCHABLE_FIELDS`` — server-side
    enforced regardless of which keys arrive in the body.
    """

    asset_id: uuid.UUID | None = None
    type_code: str | None = Field(default=None, min_length=1, max_length=64)
    number: str | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    responsible_user_id: uuid.UUID | None = None
    notes: str | None = None
    custom_field_values: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "DocumentPatchRequest":
        if not self.model_fields_set:
            raise ValueError("at least one patchable field must be provided")
        return self


class DocumentResponse(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    asset_name: str | None = None
    type_code: str
    type_display_name: str | None = None
    number: str | None
    issue_date: date | None
    expiry_date: date | None
    responsible_user_id: uuid.UUID | None
    responsible_user_name: str | None = None
    status: str
    urgency_status: str
    notes: str | None
    created_by: uuid.UUID
    updated_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    custom_field_values: dict[str, Any] = Field(default_factory=dict)


class BulkArchiveRequest(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)


class BulkArchiveResponse(BaseModel):
    archived: int
    skipped: int


# ---------------------------------------------------------------------------
# Attachment schemas
# ---------------------------------------------------------------------------


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_by: uuid.UUID
    created_at: datetime


class SignedUrlResponse(BaseModel):
    url: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# Export schemas
# ---------------------------------------------------------------------------


class ExportRequestBody(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    visible_columns: list[str] = Field(default_factory=list)


class ExportJobResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    file_path: str | None = None
    error: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginationParams(BaseModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


# ---------------------------------------------------------------------------
# Custom field schemas (flexible-document-fields)
# ---------------------------------------------------------------------------


class CustomFieldRequest(BaseModel):
    """Schema for a single custom field descriptor (for PUT body)."""

    key: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,63}$")
    display_name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., pattern=r"^(text|number|date|enum)$")
    required: bool = False
    options: list[str] | None = None


class UpdateCustomFieldSchemaRequest(BaseModel):
    """Body for PUT /admin/document-types/{type_code}/custom-fields."""

    fields: list[CustomFieldRequest]


class CustomFieldResponse(BaseModel):
    """Response shape for a single custom field descriptor."""

    key: str
    display_name: str
    type: str
    required: bool
    options: list[str] | None


class CustomFieldSchemaResponse(BaseModel):
    """Response for GET /admin/document-types/{type_code}/custom-fields."""

    fields: list[CustomFieldResponse]


class ColumnInfoResponse(BaseModel):
    """A single column classification result from import preview."""

    header: str
    matched_to: str
    suggested_type: str | None = None
    # Field name `sample_values` per ADR-0007 §4 — frontend ImportXlsxDialog
    # reads col.sample_values directly. Renamed from `samples` to match.
    sample_values: list[Any] = Field(default_factory=list)


class ImportPreviewResponse(BaseModel):
    """Response from POST /admin/import/preview."""

    import_session_id: str
    rows_total: int
    known_columns: list[ColumnInfoResponse]
    unknown_columns: list[ColumnInfoResponse]


class ImportDecisionRequest(BaseModel):
    """A single decision for an unknown column."""

    header: str
    action: str = Field(..., pattern=r"^(create_new|map_to_existing|rename|skip)$")
    new_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    target_type: str | None = None
    field_type: str | None = Field(default=None, pattern=r"^(text|number|date|enum)$")
    mapped_to_field: str | None = None
    display_name: str | None = Field(default=None, max_length=100)
    apply_to_all_types: bool = False


class ImportConfirmRequest(BaseModel):
    """Body for POST /admin/import/confirm."""

    import_session_id: str
    decisions: list[ImportDecisionRequest]


class ImportRowErrorResponse(BaseModel):
    row_index: int
    error: str


class ImportConfirmResponse(BaseModel):
    """Response from POST /admin/import/confirm."""

    rows_imported: int
    rows_failed: int
    fields_added: list[dict[str, str]]
    errors: list[ImportRowErrorResponse]


# ---------------------------------------------------------------------------
# Distinct-values schemas (v1.24.0 — column-filter autocomplete)
# ---------------------------------------------------------------------------


class DistinctValueItemResponse(BaseModel):
    """Single distinct value with its document count."""

    value: str
    count: int


class DistinctValuesResponse(BaseModel):
    """Response for GET /documents/distinct-values.

    total_distinct is the count of ALL unique values in the DB (ignoring q and limit).
    FE uses this to show «ещё N значений не показаны» hint.
    truncated is True when len(values) == limit (there may be more).
    """

    field: str
    values: list[DistinctValueItemResponse]
    total_distinct: int
    truncated: bool
    # v1.24.6 — for custom-field filters, count of docs where the field is
    # missing or empty. FE renders extra «Не задано (N)» checkbox when > 0.
    null_count: int = 0
