# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""BootstrapAdmin use case — ADR-0004 §3 / Phase 1.

Creates (or re-bootstraps) the first Space-admin user via CLI.

Rules:
- Always creates with role=admin, must_change_password=True, totp_secret_enc=SENTINEL.
- Actor is always ACTOR_SYSTEM_MIGRATOR (no JWT, no web session).
- OTP TTL is 24 h (distinct from the 10-min runtime invite OTP).
- Redis key prefix: ``bootstrap:otp:<email>`` (NOT the same as admin:otp:<user_id>).
- Idempotent on TOTP-less users: rotates OTP, emits auth.invitation.resent.v1.
- Blocks (raises UserHasActiveTotpError) if the user has totp_secret_enc set
  (i.e. has completed first-login enrollment).

ROLE SEPARATION (ADR-0006):
- This use case ALWAYS creates role='admin'. It CANNOT create super_admin users.
- make admin-create calls only this use case.
- To create a super_admin, use make superadmin-create (bootstrap_super_admin.py).
- There is no --role flag, no path that produces super_admin from this use case.

OTP format: XXXX-XXXX-XXXX (12 uppercase alphanumeric chars, grouped in fours).
The OTP itself must NEVER appear in log output or audit event payloads.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass

from lotsman_shared.actors import ACTOR_SYSTEM_MIGRATOR

from auth_service.application.dto import BootstrapAdminCommand, BootstrapAdminDTO
from auth_service.application.ports import (
    EventOutbox,
    PasswordHasher,
    RedisBootstrapOtpStore,
    UserRepository,
)
from auth_service.domain.entities import User
from auth_service.domain.errors import AuthDomainError
from auth_service.domain.events import InvitationResent, UserBootstrapped
from auth_service.domain.value_objects import Email

_OTP_ALPHABET = string.ascii_uppercase + string.digits
_OTP_SEGMENT_LEN = 4
_OTP_NUM_SEGMENTS = 3


def _generate_otp() -> str:
    """Generate a 12-char uppercase alphanumeric OTP grouped as XXXX-XXXX-XXXX."""
    segments = [
        "".join(secrets.choice(_OTP_ALPHABET) for _ in range(_OTP_SEGMENT_LEN))
        for _ in range(_OTP_NUM_SEGMENTS)
    ]
    return "-".join(segments)


class UserHasActiveTotpError(AuthDomainError):
    """User already has an active TOTP — bootstrap is blocked (ADR-0004 §3)."""

    status_code = 409
    default_message = (
        "user has active TOTP — use /admin/users password reset, "
        "or §7.2 of super-admin-runbook for full recovery"
    )


@dataclass(slots=True)
class BootstrapAdmin:
    """Bootstrap the first (or recovery) Space-admin via CLI.

    Accepts:
        user_repo: UserRepository
        hasher: PasswordHasher
        otp_store: RedisBootstrapOtpStore
        outbox: EventOutbox

    Returns BootstrapAdminDTO with plaintext OTP.
    The caller (CLI script) is responsible for printing the OTP to stdout
    and MUST NOT pass it to any logger.
    """

    user_repo: UserRepository
    hasher: PasswordHasher
    otp_store: RedisBootstrapOtpStore
    outbox: EventOutbox

    async def execute(self, *, cmd: BootstrapAdminCommand) -> BootstrapAdminDTO:
        # Normalise + validate email via value object
        email_vo = Email(value=cmd.email)
        canonical_email = email_vo.value

        existing = await self.user_repo.get_by_email(canonical_email)

        if existing is not None and existing.has_totp_enrolled:
            # Safety guard: never overwrite an active admin's credentials via CLI.
            raise UserHasActiveTotpError()

        otp = _generate_otp()
        password_hash = self.hasher.hash(otp)

        if existing is None:
            # --- Scenario 1: fresh email --- create new admin user
            user = User.create_new(
                email=canonical_email,
                full_name=cmd.full_name,
                password_hash=password_hash,
                role="admin",
            )
            await self.user_repo.add(user)

            await self.otp_store.set_otp(canonical_email, password_hash)

            await self.outbox.publish(
                UserBootstrapped(
                    actor_id=ACTOR_SYSTEM_MIGRATOR,
                    user_id=user.id,
                    email=canonical_email,
                    role="admin",
                ).as_envelope()
            )

            return BootstrapAdminDTO(
                user_id=user.id,
                email=canonical_email,
                oob_otp=otp,
            )

        else:
            # --- Scenario 2: user exists without TOTP --- rotate OTP idempotently
            # Overwrite password_hash with the new OTP hash, leave everything else.
            existing.password_hash = password_hash
            existing.must_change_password = True
            await self.user_repo.update(existing)

            # Invalidate old OTP in Redis and store new hash.
            await self.otp_store.delete_otp(canonical_email)
            await self.otp_store.set_otp(canonical_email, password_hash)

            await self.outbox.publish(
                InvitationResent(
                    actor_id=ACTOR_SYSTEM_MIGRATOR,
                    user_id=existing.id,
                    email=canonical_email,
                ).as_envelope()
            )

            return BootstrapAdminDTO(
                user_id=existing.id,
                email=canonical_email,
                oob_otp=otp,
            )
