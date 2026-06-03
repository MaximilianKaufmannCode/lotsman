# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Attachment endpoints.

POST   /documents/{document_id}/attachments  — upload (US-9)
GET    /attachments/{attachment_id}/download — signed URL redirect (US-10)
DELETE /attachments/{attachment_id}          — hard delete (US-11)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, UploadFile, status
from fastapi.responses import RedirectResponse

from registry_service.api.deps import (
    CurrentActor,
    DbSession,
    RequireEditor,
    get_attachment_storage,
    get_clock,
    get_mime_sniffer,
    get_request_id,
)
from registry_service.api.schemas import AttachmentResponse
from registry_service.application.dto import UploadAttachmentCommand
from registry_service.application.policies.attachment_policy import MAX_BYTES
from registry_service.application.use_cases.delete_attachment import DeleteAttachment
from registry_service.application.use_cases.download_attachment import DownloadAttachment
from registry_service.application.use_cases.upload_attachment import UploadAttachment
from registry_service.domain.errors import AttachmentTooLargeError
from registry_service.infrastructure.db.repositories import (
    SqlAttachmentRepository,
    SqlDocumentRepository,
    SqlEventOutbox,
)

router = APIRouter(tags=["attachments"])


# ---------------------------------------------------------------------------
# POST /documents/{document_id}/attachments
# ---------------------------------------------------------------------------


@router.post(
    "/documents/{document_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    document_id: uuid.UUID,
    file: UploadFile,
    session: DbSession,
    editor: RequireEditor,
    request: Request,
) -> AttachmentResponse:
    # F-020 (CWE-770) defence:
    # 1. Pre-check Content-Length header (client-controlled, but cheap fast-fail).
    cl_header = request.headers.get("content-length")
    if cl_header is not None:
        try:
            if int(cl_header) > MAX_BYTES:
                raise AttachmentTooLargeError()
        except ValueError:
            pass  # malformed header — fall through to streaming check

    # 2. Stream-read with running counter; abort as soon as we exceed MAX_BYTES.
    chunks: list[bytes] = []
    total = 0
    chunk_size = 64 * 1024  # 64 KiB
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise AttachmentTooLargeError()
        chunks.append(chunk)
    data = b"".join(chunks)

    async with session.begin():
        doc_repo = SqlDocumentRepository(session)
        att_repo = SqlAttachmentRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        mime_sniffer = get_mime_sniffer()
        storage = get_attachment_storage(request.app.state.settings)  # type: ignore[arg-type]

        use_case = UploadAttachment(
            doc_repo=doc_repo,
            attachment_repo=att_repo,
            storage=storage,  # type: ignore[arg-type]
            mime_sniffer=mime_sniffer,  # type: ignore[arg-type]
            outbox=outbox,  # type: ignore[arg-type]
            clock=clock,
        )
        dto = await use_case.execute(
            cmd=UploadAttachmentCommand(
                document_id=document_id,
                filename=file.filename or "attachment",
                content_type=file.content_type or "application/octet-stream",
                data=data,
                actor_id=editor.actor_id,
                request_id=get_request_id(request),
            )
        )

    return AttachmentResponse(**vars(dto))


# ---------------------------------------------------------------------------
# GET /documents/{document_id}/attachments  — list attachments for a document
# ---------------------------------------------------------------------------


@router.get(
    "/documents/{document_id}/attachments",
    response_model=list[AttachmentResponse],
)
async def list_attachments(
    document_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,  # viewer/editor/admin — any authenticated user
    request: Request,
) -> list[AttachmentResponse]:
    """List attachments for a document (US-9 — needed by DocumentDetailDrawer).

    Read-only — no transaction begin needed (SELECT autobegins and is cleaned
    up by the session lifecycle).
    """
    repo = SqlAttachmentRepository(session)
    items = await repo.list_for_document(document_id)
    return [
        AttachmentResponse(
            id=a.id,
            document_id=a.document_id,
            original_filename=a.original_filename,
            mime_type=a.mime_type,
            size_bytes=a.size_bytes,
            sha256=a.sha256,
            created_by=a.created_by,
            created_at=a.created_at,
        )
        for a in items
    ]


# ---------------------------------------------------------------------------
# GET /attachments/{attachment_id}/download
# ---------------------------------------------------------------------------


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(
    attachment_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    request: Request,
) -> RedirectResponse:
    att_repo = SqlAttachmentRepository(session)
    storage = get_attachment_storage(request.app.state.settings)  # type: ignore[arg-type]

    use_case = DownloadAttachment(
        attachment_repo=att_repo,
        storage=storage,  # type: ignore[arg-type]
    )
    signed = await use_case.execute(attachment_id=attachment_id)
    return RedirectResponse(url=signed.url, status_code=302)


# ---------------------------------------------------------------------------
# DELETE /attachments/{attachment_id}
# ---------------------------------------------------------------------------


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: uuid.UUID,
    session: DbSession,
    editor: RequireEditor,
    request: Request,
) -> None:
    async with session.begin():
        att_repo = SqlAttachmentRepository(session)
        doc_repo = SqlDocumentRepository(session)
        outbox = SqlEventOutbox(session)
        clock = get_clock()
        storage = get_attachment_storage(request.app.state.settings)  # type: ignore[arg-type]

        use_case = DeleteAttachment(
            attachment_repo=att_repo,
            doc_repo=doc_repo,
            storage=storage,  # type: ignore[arg-type]
            outbox=outbox,  # type: ignore[arg-type]
            clock=clock,
        )
        await use_case.execute(
            attachment_id=attachment_id,
            actor_id=editor.actor_id,
            request_id=get_request_id(request),
        )
