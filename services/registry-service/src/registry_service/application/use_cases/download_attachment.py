# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-10: Generate a signed URL for downloading an attachment (TTL=60s)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from registry_service.application.dto import SignedUrlDTO
from registry_service.application.ports import AttachmentRepository, AttachmentStorage
from registry_service.domain.errors import AttachmentNotFoundError

_SIGNED_URL_TTL_SECONDS = 60  # per spec §6 Security


@dataclass(slots=True)
class DownloadAttachment:
    attachment_repo: AttachmentRepository
    storage: AttachmentStorage

    async def execute(self, *, attachment_id: uuid.UUID) -> SignedUrlDTO:
        attachment = await self.attachment_repo.get_by_id(attachment_id)
        if attachment is None:
            raise AttachmentNotFoundError

        # Download is allowed even for archived documents (US-10 AC)
        signed = self.storage.signed_url(
            storage_path=attachment.storage_path,
            attachment_id=attachment_id,
            ttl_seconds=_SIGNED_URL_TTL_SECONDS,
        )
        expires_at = datetime.now(tz=UTC) + timedelta(seconds=_SIGNED_URL_TTL_SECONDS)
        return SignedUrlDTO(url=signed, expires_at=expires_at)
