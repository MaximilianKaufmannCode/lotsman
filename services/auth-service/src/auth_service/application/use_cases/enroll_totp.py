# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""EnrollTotp use case — US-3 (step 1 of 2).

Generates a new TOTP secret, stores it in Redis as a pending enrollment
(5-minute TTL), and returns the secret_b32 + otpauth_url for QR rendering.

The DB column auth.users.totp_secret_enc is NOT updated until confirmation.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass

from auth_service.application.dto import EnrollTotpCommand, TotpEnrollDTO
from auth_service.application.ports import RedisTotpEnrollmentStore, TotpService

# Type alias for the email-getter callable (injected at wiring time)
UserEmailGetter = Callable[[uuid.UUID], Coroutine[None, None, str]]

_ISSUER = "Лоцман"


@dataclass(slots=True)
class EnrollTotp:
    totp_service: TotpService
    enrollment_store: RedisTotpEnrollmentStore
    user_email_getter: UserEmailGetter  # lambda/callable for email lookup

    async def execute(self, *, cmd: EnrollTotpCommand) -> TotpEnrollDTO:
        # Generate a new 160-bit secret (RFC 4226 minimum)
        secret_b32 = self.totp_service.generate_secret_b32()

        # Look up user email for the otpauth URL
        email = await self.user_email_getter(cmd.user_id)

        # Store in Redis (5-minute TTL). Overwrites any prior pending secret
        # (re-enroll scenario per US-3 edge case).
        await self.enrollment_store.set_pending(cmd.user_id, secret_b32)

        otpauth_url = self.totp_service.make_otpauth_url(
            email=email, secret_b32=secret_b32, issuer=_ISSUER
        )

        return TotpEnrollDTO(secret_b32=secret_b32, otpauth_url=otpauth_url)
