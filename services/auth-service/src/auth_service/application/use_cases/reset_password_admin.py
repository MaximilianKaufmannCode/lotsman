# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ResetPasswordAdmin use case — US-7.

Admin resets a user's password:
1. Generates a new OOB OTP
2. Sets user.password_hash = argon2id(otp) and must_change_password = True
3. Revokes all user sessions
4. Emits PasswordReset + SessionRevoked events
5. Returns the plaintext OTP for the admin to communicate out-of-band

Note: ADR-0003 §5b says admin re-MFA is required. The re-MFA check is enforced
at the API layer (require_admin_re_mfa dependency) before this use case runs.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from auth_service.application.dto import PasswordResetDTO, ResetPasswordAdminCommand
from auth_service.application.ports import (
    EventOutbox,
    PasswordHasher,
    SessionRepository,
    UserRepository,
)
from auth_service.domain.errors import DeactivatedUserOperationError, UserNotFoundError
from auth_service.domain.events import PasswordReset, SessionRevoked


@dataclass(slots=True)
class ResetPasswordAdmin:
    user_repo: UserRepository
    session_repo: SessionRepository
    hasher: PasswordHasher
    outbox: EventOutbox

    async def execute(self, *, cmd: ResetPasswordAdminCommand) -> PasswordResetDTO:
        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()
        if not user.is_active:
            raise DeactivatedUserOperationError("Cannot reset password for a deactivated user")

        # Generate OOB OTP (12 URL-safe base64 chars ≈ 72 bits entropy)
        otp = secrets.token_urlsafe(9)

        # Set password to argon2id(otp) and flag must_change_password
        user.password_hash = self.hasher.hash(otp)
        user.must_change_password = True
        await self.user_repo.update(user)

        # Revoke all sessions
        revoked_count = await self.session_repo.revoke_all_for_user(cmd.target_user_id)
        for _ in range(revoked_count):
            await self.outbox.publish(
                SessionRevoked(
                    actor_id=cmd.actor_id,
                    session_id=cmd.target_user_id,  # session ids unknown here; use user_id as marker
                    target_user_id=cmd.target_user_id,
                ).as_envelope()
            )

        # Emit PasswordReset event
        await self.outbox.publish(
            PasswordReset(actor_id=cmd.actor_id, target_user_id=cmd.target_user_id).as_envelope()
        )

        return PasswordResetDTO(oob_otp=otp)
