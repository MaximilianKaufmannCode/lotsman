# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-20: Return signed URL for a completed export file, or 410 if expired."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from registry_service.application.dto import ExportJobDTO, SignedUrlDTO
from registry_service.application.ports import Clock, ExportJobRepository, ExportStorage
from registry_service.domain.errors import ExportJobExpiredError, ExportJobNotFoundError

_EXPORT_SIGNED_TTL = 300  # 5 minutes — longer than attachment TTL; files are larger


@dataclass(slots=True)
class DownloadExport:
    repo: ExportJobRepository
    storage: ExportStorage
    clock: Clock

    async def get_job(self, *, job_id: uuid.UUID) -> ExportJobDTO:
        job = await self.repo.get_by_id(job_id)
        if job is None:
            raise ExportJobNotFoundError
        return ExportJobDTO(
            id=job.id,
            requested_by=job.requested_by,
            status=job.status,
            file_path=job.file_path,
            error=job.error,
            expires_at=job.expires_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    async def get_download_url(self, *, job_id: uuid.UUID) -> SignedUrlDTO:
        job = await self.repo.get_by_id(job_id)
        if job is None:
            raise ExportJobNotFoundError

        if job.is_expired or job.file_path is None:
            raise ExportJobExpiredError

        from datetime import UTC, datetime, timedelta

        signed = self.storage.signed_url(
            storage_path=job.file_path,
            job_id=job_id,
            ttl_seconds=_EXPORT_SIGNED_TTL,
        )
        expires_at = datetime.now(tz=UTC) + timedelta(seconds=_EXPORT_SIGNED_TTL)
        return SignedUrlDTO(url=signed, expires_at=expires_at)
