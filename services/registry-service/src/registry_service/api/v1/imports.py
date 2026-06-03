# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""POST /api/v1/imports/xlsx — bulk import from a corporate Excel registry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, UploadFile, status

from registry_service.api.deps import DbSession, RequireAdmin
from registry_service.application.use_cases.import_xlsx import ImportXlsx

router = APIRouter(tags=["imports"])

_MAX_BYTES = 25 * 1024 * 1024  # 25 MiB ceiling


@router.post("/imports/xlsx", status_code=status.HTTP_200_OK)
async def import_xlsx(
    file: UploadFile,
    session: DbSession,
    admin: RequireAdmin,
) -> dict[str, Any]:
    """Import documents and assets from an .xlsx / .xlsm file (admin-only).

    Returns a JSON report with counts and per-row errors.
    """
    # Stream-read with cap (mirrors attachment endpoint defence)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_BYTES:
            return {
                "ok": False,
                "error": f"file too large (>{_MAX_BYTES // (1024 * 1024)} MiB)",
            }
        chunks.append(chunk)
    data = b"".join(chunks)

    async with session.begin():
        use_case = ImportXlsx(session=session, actor_id=admin.actor_id)
        try:
            report = await use_case.execute(data=data, filename=file.filename or "import.xlsx")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "filename": file.filename,
        "summary": {
            "total_rows": report.total_rows,
            "assets_created": report.assets_created,
            "assets_reused": report.assets_reused,
            "types_created": report.types_created,
            "documents_created": report.documents_created,
            "documents_updated": report.documents_updated,
            "skipped": report.skipped,
            "errors_count": len(report.errors),
        },
        "errors": [
            {
                "row": e.row_index,
                "company": e.company,
                "document": e.document,
                "error": e.error,
            }
            for e in report.errors[:50]  # cap response size
        ],
    }
