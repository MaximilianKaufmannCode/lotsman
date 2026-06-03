# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ReMfaCheck use case — US-22 (mid-session TOTP confirmation gate).

Verifies a fresh TOTP code, records period_index for anti-replay (US-23),
and sets a Redis flag ("mfa-verified:{user_id}:{session_id}", 5-minute TTL).

TOTP failures count toward the lockout counter.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import (
    RecordLoginAttemptCommand,
    ReMfaCheckCommand,
    ReMfaResultDTO,
)
from auth_service.application.ports import (
    EncryptionService,
    EventOutbox,
    LoginAttemptRepository,
    RedisReMfaStore,
    TotpService,
    TotpUsedCodeRepository,
    UserRepository,
)
from auth_service.application.use_cases.record_login_attempt import RecordLoginAttempt
from auth_service.domain.entities import TotpUsedCode
from auth_service.domain.errors import TotpCodeAlreadyUsedError, TotpInvalidError, UserNotFoundError
from auth_service.domain.value_objects import LoginOutcome


@dataclass(slots=True)
class ReMfaCheck:
    user_repo: UserRepository
    totp_service: TotpService
    encryption_service: EncryptionService
    totp_used_repo: TotpUsedCodeRepository
    re_mfa_store: RedisReMfaStore
    attempts_repo: LoginAttemptRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: ReMfaCheckCommand) -> ReMfaResultDTO:
        user = await self.user_repo.get_by_id(cmd.user_id)
        if user is None:
            raise UserNotFoundError()

        # Decrypt TOTP secret
        totp_secret = self.encryption_service.decrypt(user.totp_secret_enc)

        # Verify code
        if not self.totp_service.verify(totp_secret, cmd.totp_code):
            await RecordLoginAttempt(self.attempts_repo).execute(
                cmd=RecordLoginAttemptCommand(
                    email=user.email,
                    outcome=LoginOutcome.FAILED_TOTP,
                    ip_address=cmd.ip_address,
                    user_agent=None,
                )
            )
            raise TotpInvalidError("Invalid TOTP code")

        # Anti-replay (US-23)
        period_index = self.totp_service.current_period_index()
        if await self.totp_used_repo.exists(cmd.user_id, period_index):
            raise TotpCodeAlreadyUsedError()
        await self.totp_used_repo.add(
            TotpUsedCode.create(user_id=cmd.user_id, period_index=period_index)
        )

        # Record success
        await RecordLoginAttempt(self.attempts_repo).execute(
            cmd=RecordLoginAttemptCommand(
                email=user.email,
                outcome=LoginOutcome.SUCCESS,
                ip_address=cmd.ip_address,
                user_agent=None,
            )
        )

        # Set re-MFA verified flag (5-minute TTL)
        await self.re_mfa_store.set_verified(cmd.user_id, cmd.session_id)

        return ReMfaResultDTO(mfa_verified=True)
