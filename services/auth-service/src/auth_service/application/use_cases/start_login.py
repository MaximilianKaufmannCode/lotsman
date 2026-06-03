# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""StartLogin use case — US-1, US-2 (password phase).

Handles the first phase of login:
1. Check lockout (before any crypto)
2. Look up user by email
3. Reject system actors and inactive users with uniform 401
4. Verify password (argon2id); rehash if needed (US-24)
5a. OOB OTP path (first-time login) → return enrollment-scoped ticket
5b. TOTP enrolled → return pending-TOTP ticket for the client to send TOTP code

The second phase (TOTP verification) is handled by verify_totp.py.

Returns LoginPendingTotpDTO or LoginPendingEnrollDTO.
"""

from __future__ import annotations

import asyncio
import random
import secrets
from dataclasses import dataclass

from auth_service.application.dto import (
    LoginPendingEnrollDTO,
    LoginPendingTotpDTO,
    RecordLoginAttemptCommand,
    StartLoginCommand,
)
from auth_service.application.ports import (
    EventOutbox,
    LoginAttemptRepository,
    PasswordHasher,
    RedisAdminOtpStore,
    RedisPendingTotpLoginStore,
    UserRepository,
)
from auth_service.application.use_cases.check_lockout import CheckLockout
from auth_service.application.use_cases.record_login_attempt import RecordLoginAttempt
from auth_service.domain.entities import SYSTEM_PASSWORD_SENTINEL, PendingTotpLoginTicket
from auth_service.domain.errors import InvalidCredentialsError
from auth_service.domain.value_objects import LoginOutcome, TicketScope

_CONSTANT_TIME_DELAY_SECONDS = 0.15


async def _constant_time_delay() -> None:
    """Add a small constant-time delay to prevent timing-based user enumeration."""
    delay = _CONSTANT_TIME_DELAY_SECONDS + random.uniform(0, 0.05)
    await asyncio.sleep(delay)


@dataclass(slots=True)
class StartLogin:
    """Phase 1 of the login flow: credential check + lockout + rehash.

    Returns one of:
        LoginPendingTotpDTO  — password correct, TOTP enrolled → call verify_totp
        LoginPendingEnrollDTO — OOB OTP matched → must enroll TOTP + change password
    """

    user_repo: UserRepository
    attempts_repo: LoginAttemptRepository
    hasher: PasswordHasher
    oob_otp_store: RedisAdminOtpStore
    pending_totp_store: RedisPendingTotpLoginStore
    outbox: EventOutbox

    async def execute(
        self, *, cmd: StartLoginCommand
    ) -> LoginPendingTotpDTO | LoginPendingEnrollDTO:
        email = cmd.email.strip().lower()

        # 1. Check lockout BEFORE any crypto
        lockout = await CheckLockout(self.attempts_repo).execute(email=email)
        if lockout.is_locked:
            await _constant_time_delay()
            await RecordLoginAttempt(self.attempts_repo).execute(
                cmd=RecordLoginAttemptCommand(
                    email=email,
                    outcome=LoginOutcome.LOCKED,
                    ip_address=cmd.ip_address,
                    user_agent=cmd.user_agent,
                )
            )
            raise InvalidCredentialsError()

        # 2. Look up user
        user = await self.user_repo.get_by_email(email)
        if user is None:
            await _constant_time_delay()
            # Do NOT record a login attempt for non-existent email (no enumeration)
            raise InvalidCredentialsError()

        # 3. Reject system actors and inactive users
        if user.password_hash == SYSTEM_PASSWORD_SENTINEL or not user.is_active:
            await _constant_time_delay()
            await RecordLoginAttempt(self.attempts_repo).execute(
                cmd=RecordLoginAttemptCommand(
                    email=email,
                    outcome=LoginOutcome.FAILED_PASSWORD,
                    ip_address=cmd.ip_address,
                    user_agent=cmd.user_agent,
                )
            )
            raise InvalidCredentialsError()

        # 4. Verify password
        password_ok = self.hasher.verify(user.password_hash, cmd.password)

        # OOB OTP path: check if password might be an OOB OTP hash stored
        # in the DB (first-time login). The DB stores the hash of the OTP
        # as the user's password_hash.
        # The password field IS the OOB OTP — standard verify handles it.

        if not password_ok:
            await RecordLoginAttempt(self.attempts_repo).execute(
                cmd=RecordLoginAttemptCommand(
                    email=email,
                    outcome=LoginOutcome.FAILED_PASSWORD,
                    ip_address=cmd.ip_address,
                    user_agent=cmd.user_agent,
                )
            )
            raise InvalidCredentialsError()

        # 4b. Rehash if argon2 parameters have changed (US-24)
        if self.hasher.check_needs_rehash(user.password_hash):
            user.password_hash = self.hasher.hash(cmd.password)
            await self.user_repo.update(user)

        # 5. Determine next step
        if not user.has_totp_enrolled:
            # First-time login or after admin TOTP reset: must enroll
            enrollment_token = secrets.token_urlsafe(32)
            # Store enrollment-scoped token in Redis (5-minute TTL).
            # ADR-0008 D1.1 / MF-1: scope="enroll" discriminator prevents
            # cross-scope replay with the TOTP-login session_ticket.
            await self.pending_totp_store.set_ticket(enrollment_token, user.id, TicketScope.ENROLL)
            await RecordLoginAttempt(self.attempts_repo).execute(
                cmd=RecordLoginAttemptCommand(
                    email=email,
                    outcome=LoginOutcome.SUCCESS,
                    ip_address=cmd.ip_address,
                    user_agent=cmd.user_agent,
                )
            )
            return LoginPendingEnrollDTO(enrollment_token=enrollment_token)

        # 5b. Normal path: TOTP required
        ticket = PendingTotpLoginTicket.generate(user_id=user.id)
        # ADR-0008 D1.1 / MF-1: scope="login" discriminator prevents cross-scope
        # replay with enrollment tickets.
        await self.pending_totp_store.set_ticket(ticket.ticket_id, user.id, TicketScope.LOGIN)
        await RecordLoginAttempt(self.attempts_repo).execute(
            cmd=RecordLoginAttemptCommand(
                email=email,
                outcome=LoginOutcome.SUCCESS,
                ip_address=cmd.ip_address,
                user_agent=cmd.user_agent,
            )
        )
        return LoginPendingTotpDTO(session_ticket=ticket.ticket_id)
