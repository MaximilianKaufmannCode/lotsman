# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""FastAPI dependencies for audit-service."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

import structlog
from fastapi import Depends, Header, HTTPException
from lotsman_shared.internal_jwt import InternalJWTClaims, verify_internal_jwt
from sqlalchemy.ext.asyncio import AsyncSession

from audit_service.config import Settings, get_settings
from audit_service.infrastructure.db.session import get_session as _get_session

log = structlog.get_logger(__name__)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in _get_session():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def current_actor(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]  # noqa: B008
) -> InternalJWTClaims | None:
    if x_internal_token is None:
        return None
    try:
        return await verify_internal_jwt(
            settings.internal_jwt_key_audit,
            x_internal_token,
            expected_audience="audit-service",
        )
    except Exception as exc:
        log.warning("internal_jwt_invalid", error=str(exc))
        raise HTTPException(status_code=401, detail="Invalid internal token") from exc


CurrentActor = Annotated[InternalJWTClaims | None, Depends(current_actor)]
