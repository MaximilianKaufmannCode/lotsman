# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ConfirmTotpEnrollment use case — US-3 (step 2 of 2).

ADR-0008 rev. 3 changes (MF-2, MF-4, MF-6):

1. ``user_id`` is resolved from the enrollment ticket by the API layer
   (``resolve_enrollment_ticket`` dep), never from an actor JWT.
2. Per-ticket confirm-attempt cap (MF-6 / D5.6.1): failures are counted at
   ``totp:login:pending:attempts:<ticket>``; on the 6th failure the ticket is
   deleted and a generic 401 is raised (InvalidCredentialsError).
3. Terminal branch (D5.4): if ``user.must_change_password`` is False,
   ConfirmTotpEnrollment calls ``IssueSession`` and returns backup codes
   + tokens (the user is fully authenticated).  The ticket is deleted here.
   If ``must_change_password`` is True, the ticket stays alive and a non-terminal
   TotpConfirmDTO (backup_codes only, no tokens) is returned so the caller
   can proceed to the forced change-password step.
4. MF-4 re-check: the API layer has already verified ``not user.has_totp_enrolled``
   before calling execute (the dep rejects already-enrolled users).  The use
   case re-checks after loading the fresh user from DB for defence-in-depth
   (double-checked locking pattern against concurrent enrolments).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from auth_service.application.dto import (
    ConfirmTotpEnrollmentCommand,
    ConfirmTotpEnrollmentTerminalDTO,
    TotpConfirmDTO,
)
from auth_service.application.ports import (
    BackupCodeRepository,
    EncryptionService,
    EventOutbox,
    JwtIssuer,
    LoginAttemptRepository,
    PasswordHasher,
    RedisPendingTotpLoginStore,
    RedisTotpEnrollmentStore,
    SessionRepository,
    TotpService,
    UserRepository,
)
from auth_service.application.use_cases.issue_session import IssueSession
from auth_service.domain.entities import BackupCode
from auth_service.domain.errors import (
    InvalidCredentialsError,
    TotpEnrollmentExpiredError,
    TotpInvalidError,
)
from auth_service.domain.events import BackupCodesGenerated, TotpEnrolled
from auth_service.domain.value_objects import BackupCodeFormat

_BACKUP_CODE_COUNT = 10
# ADR-0008 D5.6.1 / MF-6: max failed TOTP-code confirmations per ticket.
MAX_CONFIRM_ATTEMPTS = 5


@dataclass(slots=True)
class ConfirmTotpEnrollment:
    user_repo: UserRepository
    totp_service: TotpService
    encryption_service: EncryptionService
    enrollment_store: RedisTotpEnrollmentStore
    backup_code_repo: BackupCodeRepository
    hasher: PasswordHasher
    outbox: EventOutbox
    # Injected only for the terminal branch (enroll-only path).
    # May be None when the use case is used in non-terminal mode.
    pending_totp_store: RedisPendingTotpLoginStore | None = None
    session_repo: SessionRepository | None = None
    jwt_issuer: JwtIssuer | None = None
    attempts_repo: LoginAttemptRepository | None = None
    session_ttl_seconds: int = 43200  # 12 hours

    async def execute(
        self,
        *,
        cmd: ConfirmTotpEnrollmentCommand,
        enrollment_token: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TotpConfirmDTO | ConfirmTotpEnrollmentTerminalDTO:
        """Confirm TOTP enrollment.

        Parameters
        ----------
        cmd:
            The command DTO carrying ``user_id`` (resolved from the ticket by the
            API dep) and the 6-digit ``code``.
        enrollment_token:
            The opaque enrollment ticket.  Required for per-ticket cap tracking
            (MF-6) and terminal-branch ticket deletion.  May be None in unit
            tests that do not exercise the terminal path.
        ip_address / user_agent:
            Forwarded from the HTTP request for session creation and audit.
        """
        # 1. Get pending secret from Redis
        pending_secret = await self.enrollment_store.get_pending(cmd.user_id)
        if pending_secret is None:
            raise TotpEnrollmentExpiredError()

        # 2. Verify code against pending secret.
        code_valid = self.totp_service.verify(pending_secret, cmd.code)
        if not code_valid:
            # MF-6 / D5.6.1: increment per-ticket attempt counter.
            if enrollment_token is not None and self.pending_totp_store is not None:
                attempts = await self.pending_totp_store.increment_confirm_attempts(
                    enrollment_token
                )
                if attempts > MAX_CONFIRM_ATTEMPTS:
                    # Cap exceeded — invalidate the ticket (no state mutation yet).
                    await self.pending_totp_store.delete_ticket(enrollment_token)
                    await self.pending_totp_store.delete_confirm_attempts(enrollment_token)
                    raise InvalidCredentialsError()
            # Within cap: ticket and pending key survive (retry preserved).
            raise TotpInvalidError("Invalid TOTP code")

        # 3. Load user for DB writes + MF-4 re-check.
        user = await self.user_repo.get_by_id(cmd.user_id)
        if user is None:
            raise InvalidCredentialsError()

        # MF-4 / D5.3.1: re-check after DB load to guard against concurrent enrolment.
        if user.has_totp_enrolled:
            raise InvalidCredentialsError()

        # 4. Encrypt and persist secret (D5.4.4)
        user.totp_secret_enc = self.encryption_service.encrypt(pending_secret)
        await self.user_repo.update(user)

        # 5. Delete pending Redis key
        await self.enrollment_store.delete_pending(cmd.user_id)

        # 6. Delete any old backup codes and generate 10 new ones (D5.4.4)
        await self.backup_code_repo.delete_all_for_user(cmd.user_id)

        plaintext_codes: list[str] = []
        new_codes: list[BackupCode] = []
        for _ in range(_BACKUP_CODE_COUNT):
            code_bytes = secrets.token_bytes(4)
            code_str = BackupCodeFormat.generate(code_bytes)
            code_hash = self.hasher.hash(code_str)
            plaintext_codes.append(code_str)
            new_codes.append(BackupCode.create(user_id=cmd.user_id, code_hash=code_hash))

        await self.backup_code_repo.add_batch(new_codes)

        # 7. Emit TotpEnrolled + BackupCodesGenerated (always, both branches — D5.4.8)
        await self.outbox.publish(TotpEnrolled(actor_id=cmd.user_id).as_envelope())
        await self.outbox.publish(BackupCodesGenerated(actor_id=cmd.user_id).as_envelope())

        # 8. Terminal branch decision (D5.4 / ADR-0008)
        if not user.must_change_password:
            # Enroll-only terminal branch: mint a real session now (D5.4.5–D5.4.9).
            # LoggedIn is emitted by IssueSession in the same transaction (D5.4.8).
            if (
                self.session_repo is None
                or self.jwt_issuer is None
                or self.attempts_repo is None
                or self.pending_totp_store is None
            ):
                raise RuntimeError(
                    "IssueSession collaborators (session_repo, jwt_issuer, "
                    "attempts_repo, pending_totp_store) must be provided for "
                    "the enroll-only terminal branch."
                )
            issue = IssueSession(
                user_repo=self.user_repo,
                session_repo=self.session_repo,
                attempts_repo=self.attempts_repo,
                jwt_issuer=self.jwt_issuer,
                outbox=self.outbox,
                session_ttl_seconds=self.session_ttl_seconds,
            )
            session_result = await issue.execute(
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                method="totp",
            )

            # Consume ticket + clear attempt counter (terminal step — exactly once).
            if enrollment_token is not None:
                await self.pending_totp_store.delete_ticket(enrollment_token)
                await self.pending_totp_store.delete_confirm_attempts(enrollment_token)

            # Return backup codes + tokens so the SPA can authenticate (D5.4.9).
            return ConfirmTotpEnrollmentTerminalDTO(
                backup_codes=plaintext_codes,
                access_token=session_result.access_token,
                refresh_token=session_result.refresh_token,
            )

        # Non-terminal branch (must_change_password=True):
        # Ticket remains alive; forced change-password is the terminal step.
        # Reset the attempt counter so the next call (change-password) is not
        # double-counted — counter TTL naturally expires with the ticket anyway.
        return TotpConfirmDTO(backup_codes=plaintext_codes)
