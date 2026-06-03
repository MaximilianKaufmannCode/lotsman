# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Admin two-step xlsx import endpoints.

POST /api/v1/admin/import/preview  — parse xlsx, classify headers, store session
POST /api/v1/admin/import/confirm  — apply decisions, insert documents

Re-MFA for confirm is enforced at BFF level (ADR-0006 §5).
The registry-service trusts the internal JWT issued after BFF TOTP check.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, UploadFile, status

from registry_service.api.deps import (
    AppSettings,
    DbSession,
    RequireAdmin,
    get_clock,
    get_request_id,
)
from registry_service.api.schemas import (
    ColumnInfoResponse,
    ImportConfirmRequest,
    ImportConfirmResponse,
    ImportPreviewResponse,
    ImportRowErrorResponse,
)
from registry_service.application.dto import ImportConfirmCommand, ImportDecision
from registry_service.application.use_cases.import_xlsx_confirm import ImportXlsxConfirm
from registry_service.application.use_cases.import_xlsx_preview import ImportXlsxPreview
from registry_service.infrastructure.db.repositories import (
    SqlAssetRepository,
    SqlDocumentRepository,
    SqlDocumentTypeRepository,
    SqlEventOutbox,
)

router = APIRouter(prefix="/admin", tags=["admin", "imports"])

_MAX_BYTES = 25 * 1024 * 1024  # 25 MiB


@router.post(
    "/import/preview",
    response_model=ImportPreviewResponse,
    status_code=status.HTTP_200_OK,
)
async def import_xlsx_preview(
    file: UploadFile,
    session: DbSession,
    admin: RequireAdmin,
    settings: AppSettings,
    request: Request,
) -> ImportPreviewResponse:
    """Parse an xlsx file and classify headers (admin-only).

    Returns known/unknown column classification and a session ID for the confirm step.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_BYTES:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {_MAX_BYTES // (1024 * 1024)} MiB limit",
            )
        chunks.append(chunk)
    file_bytes = b"".join(chunks)

    async with session.begin():
        type_repo = SqlDocumentTypeRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = ImportXlsxPreview(
            type_repo=type_repo,
            outbox=outbox,
            clock=clock,
            redis_url=settings.redis_url,
        )
        dto = await use_case.execute(
            actor_id=admin.actor_id,
            file_bytes=file_bytes,
            request_id=get_request_id(request),
        )

    return ImportPreviewResponse(
        import_session_id=dto.import_session_id,
        rows_total=dto.rows_total,
        known_columns=[
            ColumnInfoResponse(
                header=c.header,
                matched_to=c.matched_to,
                suggested_type=c.suggested_type,
                sample_values=[_jsonify(s) for s in c.samples],
            )
            for c in dto.known_columns
        ],
        unknown_columns=[
            ColumnInfoResponse(
                header=c.header,
                matched_to=c.matched_to,
                suggested_type=c.suggested_type,
                sample_values=[_jsonify(s) for s in c.samples],
            )
            for c in dto.unknown_columns
        ],
    )


@router.post(
    "/import/confirm",
    response_model=ImportConfirmResponse,
    status_code=status.HTTP_200_OK,
)
async def import_xlsx_confirm(
    body: ImportConfirmRequest,
    session: DbSession,
    admin: RequireAdmin,
    settings: AppSettings,
    request: Request,
) -> ImportConfirmResponse:
    """Apply import decisions and bulk-insert documents (admin-only, requires re-MFA via BFF).

    The BFF strips totp_code and verifies it before forwarding this request.
    """
    async with session.begin():
        doc_repo = SqlDocumentRepository(session)
        asset_repo = SqlAssetRepository(session)
        type_repo = SqlDocumentTypeRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()

        use_case = ImportXlsxConfirm(
            doc_repo=doc_repo,
            asset_repo=asset_repo,
            type_repo=type_repo,
            outbox=outbox,
            clock=clock,
            redis_url=settings.redis_url,
        )

        decisions = [
            ImportDecision(
                header=d.header,
                action=d.action,
                new_key=d.new_key,
                target_type=d.target_type,
                field_type=d.field_type,
                mapped_to_field=d.mapped_to_field,
                display_name=d.display_name,
                apply_to_all_types=d.apply_to_all_types,
            )
            for d in body.decisions
        ]

        result = await use_case.execute(
            cmd=ImportConfirmCommand(
                import_session_id=body.import_session_id,
                decisions=decisions,
                actor_id=admin.actor_id,
                request_id=get_request_id(request),
            )
        )

    return ImportConfirmResponse(
        rows_imported=result.rows_imported,
        rows_failed=result.rows_failed,
        fields_added=result.fields_added,
        errors=[
            ImportRowErrorResponse(row_index=e.row_index, error=e.error) for e in result.errors
        ],
    )


def _jsonify(v: Any) -> Any:
    """Convert a sample cell value to a JSON-safe type."""
    from datetime import date, datetime

    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)
