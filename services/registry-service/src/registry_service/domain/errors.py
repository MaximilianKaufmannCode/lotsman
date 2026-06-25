# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Typed domain errors for registry-service.

All inherit from DomainError (shared kernel). The API layer maps these to
HTTP responses via the registered exception handler. Use cases and domain
code never import HTTP status codes (Iron Rule 7).
"""

from __future__ import annotations

from lotsman_shared.errors import DomainError


class RegistryDomainError(DomainError):
    """Base for all registry-service domain errors."""


# ---------------------------------------------------------------------------
# Not-found errors (404)
# ---------------------------------------------------------------------------


class AssetNotFoundError(RegistryDomainError):
    status_code = 404
    default_message = "Asset not found"


class DocumentNotFoundError(RegistryDomainError):
    status_code = 404
    default_message = "Document not found"


class AttachmentNotFoundError(RegistryDomainError):
    status_code = 404
    default_message = "Attachment not found"


class ExportJobNotFoundError(RegistryDomainError):
    status_code = 404
    default_message = "Export job not found"


class DocumentTypeNotFoundError(RegistryDomainError):
    status_code = 404
    default_message = "Document type not found"


# ---------------------------------------------------------------------------
# Validation errors (422)
# ---------------------------------------------------------------------------


class InnInvalidError(RegistryDomainError):
    status_code = 422
    default_message = "INN format or checksum is invalid"


class AssetNameInvalidError(RegistryDomainError):
    status_code = 422
    default_message = "Asset name is invalid"


class RequiredFieldMissingError(RegistryDomainError):
    status_code = 422
    default_message = "Required field is missing"


# ---------------------------------------------------------------------------
# Attachment errors
# ---------------------------------------------------------------------------


class AttachmentTooLargeError(RegistryDomainError):
    status_code = 413
    default_message = "Файл превышает допустимый размер 25 МиБ"


class AttachmentMimeRejectedError(RegistryDomainError):
    status_code = 415
    default_message = "Unsupported media type"


class AttachmentVirusScanError(RegistryDomainError):
    status_code = 422
    default_message = "Файл не прошёл антивирусную проверку"


class AttachmentArchivedDocumentError(RegistryDomainError):
    status_code = 409
    default_message = "Нельзя добавить вложение к архивному документу"


class AttachmentDeleteArchivedDocumentError(RegistryDomainError):
    status_code = 409
    default_message = "Нельзя удалить вложение из архивного документа"


# ---------------------------------------------------------------------------
# Bulk operation errors
# ---------------------------------------------------------------------------


class BulkLimitExceededError(RegistryDomainError):
    status_code = 400
    default_message = "Bulk operation limited to 100 rows"


# ---------------------------------------------------------------------------
# Export errors
# ---------------------------------------------------------------------------


class ExportJobExpiredError(RegistryDomainError):
    status_code = 410
    default_message = "Файл экспорта истёк. Создайте новый экспорт."


# ---------------------------------------------------------------------------
# Asset conflict errors
# ---------------------------------------------------------------------------


class AssetAlreadyExistsError(RegistryDomainError):
    status_code = 409
    default_message = "Компания с таким названием уже существует"


class InvalidAssetStatusError(RegistryDomainError):
    status_code = 422
    default_message = "Invalid asset status. Must be one of: active, liquidating, archived"


class FilterConflictError(RegistryDomainError):
    """Raised when mutually exclusive filter parameters are combined.

    Specifically: expiry_is_null=true cannot be combined with expiry_from/expiry_to
    because NULL expiry dates have no date value to range-compare against.
    The backend applies expiry_is_null exclusively when set; combining it
    with a date range is a logical contradiction.
    """

    status_code = 422
    default_message = "Conflicting filter parameters: expiry_is_null cannot be used with expiry_from/expiry_to"


class AssetArchivedError(RegistryDomainError):
    status_code = 404
    default_message = "Asset not found or archived"


# ---------------------------------------------------------------------------
# Permission errors
# ---------------------------------------------------------------------------


class RoleForbiddenError(RegistryDomainError):
    status_code = 403
    default_message = "Forbidden"


# ---------------------------------------------------------------------------
# Custom field errors (Deliverable 1 — flexible-document-fields)
# ---------------------------------------------------------------------------


class CustomFieldSchemaValidationError(RegistryDomainError):
    """Raised when a custom field schema definition fails validation."""

    status_code = 422
    default_message = "Custom field schema is invalid"
    code = "CUSTOM_FIELD_VALIDATION"


class UnknownColumnsError(RegistryDomainError):
    """Raised during xlsx import when headers cannot be mapped to any known field.

    payload.unknown_columns contains the list of unrecognised column headers.
    Use /admin/import/preview to classify them before importing.
    """

    status_code = 409
    default_message = "Unknown columns detected — use /admin/import/preview"
    code = "UNKNOWN_COLUMNS"

    def __init__(
        self,
        message: str | None = None,
        unknown_columns: list[str] | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.unknown_columns: list[str] = unknown_columns or []


class ImportSessionExpiredError(RegistryDomainError):
    """Raised when the import session (stored in Redis) has expired."""

    status_code = 410
    default_message = "Import session has expired — start a new preview"
    code = "SESSION_EXPIRED"


class ImportSessionNotFoundError(RegistryDomainError):
    """Raised when the import session ID does not exist in Redis."""

    status_code = 404
    default_message = "Import session not found"
    code = "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Distinct-values errors (v1.24.0 — column-filter autocomplete)
# ---------------------------------------------------------------------------


class DateFieldDistinctNotSupported(RegistryDomainError):
    """Raised when distinct-values is requested for a date column.

    Date columns have high cardinality; the UI shows a DatePicker
    instead of an autocomplete list. API returns 422 per spec §6.
    """

    status_code = 422
    code = "DATE_FIELD_DISTINCT_NOT_SUPPORTED"
    default_message = (
        "Distinct values for date columns is not supported. "
        "Use date range filter directly."
    )

    def __init__(self, field: str) -> None:
        super().__init__(self.default_message)
        self.field = field


class UnknownDistinctField(RegistryDomainError):
    """Raised when the requested field is not a known system field or schema key."""

    status_code = 422
    code = "UNKNOWN_DISTINCT_FIELD"

    def __init__(self, field: str) -> None:
        super().__init__(f"Unknown field: {field}")
        self.field = field
