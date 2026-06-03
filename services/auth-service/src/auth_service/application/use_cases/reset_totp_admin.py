# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ResetTotpAdmin use case — US-16.

Admin resets a target user's TOTP:
1. Re-MFA: verify admin's own TOTP code (fails with 403 on wrong code)
2. Set totp_secret_enc = TOTP_SENTINEL (b'\x00')
3. Delete all backup codes for target
4. Revoke all sessions for target
5. Emit TotpReset + SessionRevokedAll events

Self-reset is rejected (admin cannot reset their own TOTP — must contact another admin).
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import ResetTotpAdminCommand
from auth_service.application.ports import (
    BackupCodeRepository,
    EncryptionService,
    EventOutbox,
    SessionRepository,
    TotpService,
    UserRepository,
)
from auth_service.domain.entities import TOTP_SENTINEL
from auth_service.domain.errors import (
    ReMfaFailedError,
    SelfActionForbiddenError,
    UserNotFoundError,
)
from auth_service.domain.events import SessionRevokedAll, TotpReset


@dataclass(slots=True)
class ResetTotpAdmin:
    user_repo: UserRepository
    session_repo: SessionRepository
    backup_code_repo: BackupCodeRepository
    totp_service: TotpService
    encryption_service: EncryptionService
    outbox: EventOutbox

    async def execute(self, *, cmd: ResetTotpAdminCommand) -> None:
        # Self-reset guard
        if cmd.actor_id == cmd.target_user_id:
            raise SelfActionForbiddenError("Cannot reset your own TOTP; contact another admin")

        # Load admin user and verify their TOTP code
        admin = await self.user_repo.get_by_id(cmd.actor_id)
        if admin is None:
            raise UserNotFoundError("Admin not found")

        admin_totp_secret = self.encryption_service.decrypt(admin.totp_secret_enc)
        if not self.totp_service.verify(admin_totp_secret, cmd.admin_totp_code):
            raise ReMfaFailedError()

        # Load target user
        target = await self.user_repo.get_by_id(cmd.target_user_id)
        if target is None:
            raise UserNotFoundError()

        # Reset TOTP to sentinel
        target.totp_secret_enc = TOTP_SENTINEL
        await self.user_repo.update(target)

        # Delete backup codes
        await self.backup_code_repo.delete_all_for_user(cmd.target_user_id)

        # Revoke all target sessions
        revoked_count = await self.session_repo.revoke_all_for_user(cmd.target_user_id)

        # Emit events
        await self.outbox.publish(
            TotpReset(actor_id=cmd.actor_id, target_user_id=cmd.target_user_id).as_envelope()
        )
        if revoked_count > 0:
            await self.outbox.publish(
                SessionRevokedAll(
                    actor_id=cmd.actor_id,
                    target_user_id=cmd.target_user_id,
                    revoked_count=revoked_count,
                ).as_envelope()
            )
