# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Local filesystem attachment storage backed by a Docker volume.

Files are stored at:
  <volume_root>/attachments/<YYYY>/<MM>/<attachment_id>.<ext>

All paths are stored relative to volume_root in the DB (storage_path column).
The absolute path is reconstructed at read time by prepending volume_root.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiofiles
import aiofiles.os
import structlog

log = structlog.get_logger(__name__)


class LocalFilesystemStorage:
    """AttachmentStorage backed by a local volume directory.

    Args:
        volume_root: Absolute path to the attachments volume root.
                     Injected from settings.attachments_volume_root.
    """

    def __init__(self, volume_root: str) -> None:
        self._root = Path(volume_root)

    def _full_path(self, storage_path: str) -> Path:
        return self._root / storage_path

    async def save(
        self,
        *,
        data: bytes,
        document_id: uuid.UUID,
        attachment_id: uuid.UUID,
        original_filename: str,
    ) -> str:
        """Persist bytes to disk. Returns the relative storage_path."""
        now = datetime.now(tz=UTC)
        ext = Path(original_filename).suffix or ""
        rel_path = f"attachments/{now.year}/{now.month:02d}/{attachment_id}{ext}"
        full = self._root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(full, "wb") as f:
            await f.write(data)

        log.info(
            "attachment_saved",
            storage_path=rel_path,
            size_bytes=len(data),
            attachment_id=str(attachment_id),
        )
        return rel_path

    async def delete(self, storage_path: str) -> None:
        """Remove a file from disk. Idempotent — no error if absent."""
        full = self._full_path(storage_path)
        try:
            await aiofiles.os.remove(str(full))
            log.info("attachment_deleted", storage_path=storage_path)
        except FileNotFoundError:
            log.debug("attachment_delete_noop", storage_path=storage_path)
        except Exception:
            log.exception("attachment_delete_failed", storage_path=storage_path)

    def signed_url(
        self,
        *,
        storage_path: str,
        attachment_id: uuid.UUID,
        ttl_seconds: int,
    ) -> str:
        """Generate an HMAC-SHA256 signed URL for file serving.

        Delegates to the signed_url module which embeds the HMAC key from
        settings. The returned URL is routed through nginx/file-server.
        """
        from registry_service.infrastructure.storage.signed_url import make_signed_url

        return make_signed_url(
            storage_path=storage_path,
            resource_id=attachment_id,
            ttl_seconds=ttl_seconds,
            resource_type="attachment",
        )
