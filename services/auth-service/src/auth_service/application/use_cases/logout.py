# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Logout use case — US-8.

Revokes the refresh token's session (idempotent).
The access JWT remains valid until its exp (≤15 min); this is documented and accepted
per ADR-0003 §13.

Returns None; callers clear the cookie regardless.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from auth_service.application.dto import LogoutCommand
from auth_service.application.ports import EventOutbox, SessionRepository


@dataclass(slots=True)
class Logout:
    session_repo: SessionRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: LogoutCommand, actor_id: uuid.UUID) -> None:  # noqa: F821

        if cmd.refresh_token is None:
            return  # idempotent — nothing to revoke

        refresh_hash = hashlib.sha256(cmd.refresh_token.encode()).hexdigest()
        session = await self.session_repo.get_by_refresh_hash(refresh_hash)

        if session is None or session.revoked_at is not None:
            return  # already revoked or unknown token — idempotent

        await self.session_repo.revoke(session.id)

        from auth_service.domain.events import LoggedOut

        await self.outbox.publish(LoggedOut(actor_id=actor_id, session_id=session.id).as_envelope())
