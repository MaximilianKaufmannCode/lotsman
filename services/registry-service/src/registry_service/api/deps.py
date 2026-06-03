# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""FastAPI dependencies for registry-service.

Provides:
  - DB session
  - Internal JWT actor extraction
  - Role guards (require_editor, require_admin)
  - Pagination + filter query parsing
  - Use case factories (DI wiring)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated, Any

import structlog
from fastapi import Depends, Header, HTTPException, Query, Request
from lotsman_shared.internal_jwt import InternalJWTClaims, verify_internal_jwt
from sqlalchemy.ext.asyncio import AsyncSession

from registry_service.config import Settings, get_settings
from registry_service.infrastructure.db.session import get_session as _get_session

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# DB session
# ---------------------------------------------------------------------------


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in _get_session():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


# ---------------------------------------------------------------------------
# Internal JWT actor
# ---------------------------------------------------------------------------


async def current_actor(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
    settings: AppSettings = None,  # type: ignore[assignment]
) -> InternalJWTClaims:
    """Require a valid internal JWT. Raises 401 if absent or invalid."""
    if x_internal_token is None:
        raise HTTPException(status_code=401, detail="Invalid internal token")
    try:
        return await verify_internal_jwt(
            settings.internal_jwt_key_registry,
            x_internal_token,
            expected_audience="registry-service",
        )
    except Exception as exc:
        log.warning("internal_jwt_invalid", error=str(exc))
        raise HTTPException(status_code=401, detail="Invalid internal token") from exc


CurrentActor = Annotated[InternalJWTClaims, Depends(current_actor)]


def get_request_id(request: Request) -> str | None:
    return request.headers.get("X-Request-Id")


# ---------------------------------------------------------------------------
# Role guards
# ---------------------------------------------------------------------------


async def require_editor(
    actor: Annotated[InternalJWTClaims, Depends(current_actor)],
) -> InternalJWTClaims:
    """Allow 'editor' and 'admin' roles; reject 'viewer'."""
    if actor.role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail="Forbidden")
    return actor


async def require_admin(
    actor: Annotated[InternalJWTClaims, Depends(current_actor)],
) -> InternalJWTClaims:
    """Allow 'admin' role only."""
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return actor


RequireEditor = Annotated[InternalJWTClaims, Depends(require_editor)]
RequireAdmin = Annotated[InternalJWTClaims, Depends(require_admin)]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginationDep:
    def __init__(
        self,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> None:
        self.offset = offset
        self.limit = limit


Pagination = Annotated[PaginationDep, Depends(PaginationDep)]


# ---------------------------------------------------------------------------
# Upstream use case factories — thin DI wiring
# ---------------------------------------------------------------------------


def get_clock() -> Any:
    from registry_service.infrastructure.clock import SystemClock

    return SystemClock()


def get_mime_sniffer() -> Any:
    from registry_service.infrastructure.mime import get_mime_sniffer as _get

    return _get()


def get_attachment_storage(
    settings: AppSettings,
) -> Any:
    from registry_service.infrastructure.storage.local_filesystem import LocalFilesystemStorage

    vol = getattr(settings, "attachments_volume_root", "/vol/attachments")
    return LocalFilesystemStorage(vol)


def get_export_storage(
    settings: AppSettings,
) -> Any:
    from registry_service.infrastructure.storage.local_filesystem import LocalFilesystemStorage

    vol = getattr(settings, "exports_volume_root", "/vol/exports")
    return LocalFilesystemStorage(vol)


def get_audit_client(
    request: Request,
) -> Any:
    return request.app.state.audit_client  # type: ignore[no-any-return]
