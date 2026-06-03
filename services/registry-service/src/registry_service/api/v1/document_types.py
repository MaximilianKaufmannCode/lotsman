# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""GET/POST/PATCH endpoints for document types.

Role requirements:
  - GET:    any authenticated actor
  - POST/PATCH: admin only
  - custom-fields GET: admin only
  - custom-fields PUT: admin only
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from registry_service.api.deps import (
    CurrentActor,
    DbSession,
    RequireAdmin,
    get_clock,
    get_request_id,
)
from registry_service.api.schemas import (
    CustomFieldResponse,
    CustomFieldSchemaResponse,
    DocumentTypeResponse,
    DocumentTypeUpsertRequest,
    UpdateCustomFieldSchemaRequest,
)
from registry_service.application.dto import (
    UpdateCustomFieldSchemaCommand,
    UpsertDocumentTypeCommand,
)
from registry_service.application.use_cases.get_custom_field_schema import GetCustomFieldSchema
from registry_service.application.use_cases.list_document_types import ListDocumentTypes
from registry_service.application.use_cases.update_custom_field_schema import (
    UpdateCustomFieldSchema,
)
from registry_service.application.use_cases.upsert_document_type import UpsertDocumentType
from registry_service.domain.custom_fields import CustomField, FieldType
from registry_service.infrastructure.db.repositories import (
    SqlDocumentTypeRepository,
    SqlEventOutbox,
)

router = APIRouter(prefix="/document-types", tags=["document-types"])


@router.get("", response_model=list[DocumentTypeResponse])
async def list_document_types(
    session: DbSession,
    actor: CurrentActor,
) -> list[DocumentTypeResponse]:
    repo = SqlDocumentTypeRepository(session)
    use_case = ListDocumentTypes(repo=repo)
    dtos = await use_case.execute()
    return [DocumentTypeResponse(**vars(dto)) for dto in dtos]


@router.post("", response_model=DocumentTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_document_type(
    body: DocumentTypeUpsertRequest,
    session: DbSession,
    admin: RequireAdmin,
    request: Request,
) -> DocumentTypeResponse:
    async with session.begin():
        repo = SqlDocumentTypeRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = UpsertDocumentType(repo=repo, outbox=outbox, clock=clock)
        dto = await use_case.execute(
            cmd=UpsertDocumentTypeCommand(
                code=body.code,
                display_name=body.display_name,
                pre_notice_days=body.pre_notice_days,
                notify_in_day=body.notify_in_day,
                overdue_every_days=body.overdue_every_days,
                actor_id=admin.actor_id,
                request_id=get_request_id(request),
            )
        )
    return DocumentTypeResponse(**vars(dto))


@router.patch("/{code}", response_model=DocumentTypeResponse)
async def update_document_type(
    code: str,
    body: DocumentTypeUpsertRequest,
    session: DbSession,
    admin: RequireAdmin,
    request: Request,
) -> DocumentTypeResponse:
    async with session.begin():
        repo = SqlDocumentTypeRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = UpsertDocumentType(repo=repo, outbox=outbox, clock=clock)
        # Force the code from the URL path (ignore body.code for PATCH)
        cmd_body = body.model_copy(update={"code": code})
        dto = await use_case.execute(
            cmd=UpsertDocumentTypeCommand(
                code=code,
                display_name=cmd_body.display_name,
                pre_notice_days=cmd_body.pre_notice_days,
                notify_in_day=cmd_body.notify_in_day,
                overdue_every_days=cmd_body.overdue_every_days,
                actor_id=admin.actor_id,
                request_id=get_request_id(request),
            )
        )
    return DocumentTypeResponse(**vars(dto))


# ---------------------------------------------------------------------------
# Admin custom-fields endpoints (flexible-document-fields)
# ---------------------------------------------------------------------------

# Note: this router uses prefix="/document-types" so the full paths are:
# GET  /api/v1/document-types/{type_code}/custom-fields
# PUT  /api/v1/document-types/{type_code}/custom-fields
# But they need admin prefix in spec — we expose them under the existing router.


@router.get(
    "/admin/{type_code}/custom-fields",
    response_model=CustomFieldSchemaResponse,
    tags=["admin", "custom-fields"],
)
async def get_custom_field_schema(
    type_code: str,
    session: DbSession,
    admin: RequireAdmin,
) -> CustomFieldSchemaResponse:
    """Get the custom field schema for a document type (admin-only)."""
    repo = SqlDocumentTypeRepository(session)
    use_case = GetCustomFieldSchema(repo=repo)
    schema = await use_case.execute(type_code=type_code)
    return CustomFieldSchemaResponse(
        fields=[
            CustomFieldResponse(
                key=f.key,
                display_name=f.display_name,
                type=str(f.type),
                required=f.required,
                options=f.options,
            )
            for f in schema
        ]
    )


@router.put(
    "/admin/{type_code}/custom-fields",
    response_model=CustomFieldSchemaResponse,
    tags=["admin", "custom-fields"],
)
async def update_custom_field_schema(
    type_code: str,
    body: UpdateCustomFieldSchemaRequest,
    session: DbSession,
    admin: RequireAdmin,
    request: Request,
) -> CustomFieldSchemaResponse:
    """Replace the custom field schema for a document type (admin-only).

    Re-MFA is enforced at BFF level (ADR-0006 §5). The registry-service trusts
    the internal JWT, which is only issued after the BFF verifies TOTP.
    """
    async with session.begin():
        repo = SqlDocumentTypeRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        use_case = UpdateCustomFieldSchema(repo=repo, outbox=outbox, clock=clock)

        # Convert request schema items to CustomField value objects
        new_schema = [
            CustomField(
                key=f.key,
                display_name=f.display_name,
                type=FieldType(f.type),
                required=f.required,
                options=f.options,
            )
            for f in body.fields
        ]

        updated = await use_case.execute(
            cmd=UpdateCustomFieldSchemaCommand(
                type_code=type_code,
                schema=new_schema,
                actor_id=admin.actor_id,
                request_id=get_request_id(request),
            )
        )

    return CustomFieldSchemaResponse(
        fields=[
            CustomFieldResponse(
                key=f.key,
                display_name=f.display_name,
                type=str(f.type),
                required=f.required,
                options=f.options,
            )
            for f in updated
        ]
    )
