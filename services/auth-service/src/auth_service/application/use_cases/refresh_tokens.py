# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""RefreshTokens use case — US-9 (rotation-on-use).

Per ADR-0003 §8:
1. Look up session by sha256(refresh_token)
2. If revoked → reuse detected → chain-revoke all user sessions + emit high-severity event
3. If expired → 401
4. Otherwise: rotate (new session + revoke old), issue new access JWT + refresh token

Calls RecordSessionReuse internally for the reuse-detection path.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from auth_service.application.dto import LoginSuccessDTO, RefreshTokensCommand
from auth_service.application.ports import EventOutbox, JwtIssuer, SessionRepository, UserRepository
from auth_service.domain.entities import Session
from auth_service.domain.errors import InvalidCredentialsError, SessionExpiredError
from auth_service.domain.events import SessionReuseDetected, SessionRotated


@dataclass(slots=True)
class RefreshTokens:
    user_repo: UserRepository
    session_repo: SessionRepository
    jwt_issuer: JwtIssuer
    outbox: EventOutbox

    async def execute(self, *, cmd: RefreshTokensCommand) -> LoginSuccessDTO:
        refresh_hash = hashlib.sha256(cmd.refresh_token.encode()).hexdigest()

        session = await self.session_repo.get_by_refresh_hash(refresh_hash)

        if session is None:
            # Unknown token — emit audit anomaly
            await self.outbox.publish(SessionReuseDetected(actor_id=None).as_envelope())
            raise InvalidCredentialsError()

        if session.revoked_at is not None:
            # REUSE DETECTED — chain-revoke all user sessions
            await self.session_repo.revoke_all_for_user(session.user_id)
            await self.outbox.publish(SessionReuseDetected(actor_id=session.user_id).as_envelope())
            raise InvalidCredentialsError()

        now = datetime.now(tz=UTC)
        if session.expires_at <= now:
            raise SessionExpiredError()

        # Healthy rotation
        user = await self.user_repo.get_by_id(session.user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError()

        # Issue new refresh token
        new_refresh_token = secrets.token_urlsafe(32)
        new_refresh_hash = hashlib.sha256(new_refresh_token.encode()).hexdigest()

        # New session inherits original expires_at (7-day absolute — no sliding)
        new_session = Session(
            id=__import__("uuid").uuid4(),
            user_id=session.user_id,
            refresh_hash=new_refresh_hash,
            user_agent=cmd.user_agent or session.user_agent,
            ip_address=cmd.ip_address or session.ip_address,
            expires_at=session.expires_at,
            revoked_at=None,
            created_at=now,
        )
        await self.session_repo.add(new_session)
        await self.session_repo.revoke(session.id)

        # Issue new access JWT
        access_token = self.jwt_issuer.issue(
            user_id=user.id,
            email=user.email,
            role=user.role,
            session_id=new_session.id,
        )

        # Emit rotation event
        await self.outbox.publish(
            SessionRotated(
                actor_id=user.id,
                old_session_id=session.id,
                new_session_id=new_session.id,
            ).as_envelope()
        )

        return LoginSuccessDTO(access_token=access_token, refresh_token=new_refresh_token)
