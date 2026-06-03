# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""VerifyTotp use case — US-2, US-23 (TOTP phase of login).

Consumes the pending-TOTP ticket (issued by start_login), verifies the TOTP code,
enforces anti-replay (US-23), creates a session, and returns tokens.

Also handles US-5 backup-code path when backup_code is provided.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import (
    LoginSuccessDTO,
    RecordLoginAttemptCommand,
    VerifyTotpCommand,
)
from auth_service.application.ports import (
    BackupCodeRepository,
    EncryptionService,
    EventOutbox,
    JwtIssuer,
    LoginAttemptRepository,
    PasswordHasher,
    RedisPendingTotpLoginStore,
    SessionRepository,
    TotpService,
    TotpUsedCodeRepository,
    UserRepository,
)
from auth_service.application.use_cases.check_lockout import CheckLockout
from auth_service.application.use_cases.issue_session import IssueSession
from auth_service.application.use_cases.record_login_attempt import RecordLoginAttempt
from auth_service.domain.entities import TotpUsedCode
from auth_service.domain.errors import (
    BackupCodeInvalidError,
    InvalidCredentialsError,
    TotpInvalidError,
)
from auth_service.domain.value_objects import LoginOutcome, TicketScope

_BACKUP_LOW_THRESHOLD = 2
_DEFAULT_SESSION_TTL_SECONDS = 43200  # 12 hours


@dataclass(slots=True)
class VerifyTotp:
    """Phase 2 of login: verify TOTP code (or backup code) and issue tokens."""

    user_repo: UserRepository
    session_repo: SessionRepository
    attempts_repo: LoginAttemptRepository
    totp_service: TotpService
    encryption_service: EncryptionService
    backup_code_repo: BackupCodeRepository
    totp_used_repo: TotpUsedCodeRepository
    pending_totp_store: RedisPendingTotpLoginStore
    jwt_issuer: JwtIssuer
    hasher: PasswordHasher
    outbox: EventOutbox
    session_ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS

    async def execute(
        self,
        *,
        cmd: VerifyTotpCommand,
        backup_code: str | None = None,
    ) -> LoginSuccessDTO:
        # 1. Resolve pending ticket to user_id (ADR-0008 D1.3 / MF-1).
        # expected_scope=LOGIN ensures an enrollment ticket (scope="enroll")
        # is rejected here — cross-scope replay protection (INV-3).
        user_id = await self.pending_totp_store.get_user_id(
            cmd.ticket_id, expected_scope=TicketScope.LOGIN
        )
        if user_id is None:
            raise InvalidCredentialsError()

        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise InvalidCredentialsError()

        # 2. Check lockout (TOTP failures count toward the same counter)
        lockout = await CheckLockout(self.attempts_repo).execute(email=user.email)
        if lockout.is_locked:
            await RecordLoginAttempt(self.attempts_repo).execute(
                cmd=RecordLoginAttemptCommand(
                    email=user.email,
                    outcome=LoginOutcome.LOCKED,
                    ip_address=cmd.ip_address,
                    user_agent=cmd.user_agent,
                )
            )
            raise InvalidCredentialsError()

        method = "totp"

        if backup_code is not None:
            # US-5: backup code path
            from auth_service.domain.value_objects import BackupCodeFormat

            normalised = BackupCodeFormat.normalise(backup_code)
            unused_codes = await self.backup_code_repo.list_unused_for_user(user.id)
            matched_code = None
            for bc in unused_codes:
                if self.hasher.verify(bc.code_hash, normalised):
                    matched_code = bc
                    break
            if matched_code is None:
                await RecordLoginAttempt(self.attempts_repo).execute(
                    cmd=RecordLoginAttemptCommand(
                        email=user.email,
                        outcome=LoginOutcome.FAILED_TOTP,
                        ip_address=cmd.ip_address,
                        user_agent=cmd.user_agent,
                    )
                )
                raise BackupCodeInvalidError()
            await self.backup_code_repo.mark_used(matched_code.id)
            method = "backup_code"
        else:
            # 3. Decrypt TOTP secret and verify code
            totp_secret = self.encryption_service.decrypt(user.totp_secret_enc)
            code_valid = self.totp_service.verify(totp_secret, cmd.totp_code)
            if not code_valid:
                await RecordLoginAttempt(self.attempts_repo).execute(
                    cmd=RecordLoginAttemptCommand(
                        email=user.email,
                        outcome=LoginOutcome.FAILED_TOTP,
                        ip_address=cmd.ip_address,
                        user_agent=cmd.user_agent,
                    )
                )
                raise TotpInvalidError()

            # 4. Anti-replay: record period_index (US-23)
            period_index = self.totp_service.current_period_index()
            if await self.totp_used_repo.exists(user.id, period_index):
                await RecordLoginAttempt(self.attempts_repo).execute(
                    cmd=RecordLoginAttemptCommand(
                        email=user.email,
                        outcome=LoginOutcome.FAILED_TOTP,
                        ip_address=cmd.ip_address,
                        user_agent=cmd.user_agent,
                    )
                )
                raise TotpInvalidError()
            await self.totp_used_repo.add(
                TotpUsedCode.create(user_id=user.id, period_index=period_index)
            )

        # 5–10. Delegate session minting to the shared IssueSession collaborator
        # (ADR-0008 D5.4.1 / MF-2 / INV-1).
        # This is BEHAVIOUR-PRESERVING for verify_totp: it already emitted LoggedIn
        # and set last_login_at before this refactor.  IssueSession emits the same
        # events in the same transaction with the same actor_id (INV-1).
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
            ip_address=cmd.ip_address,
            user_agent=cmd.user_agent,
            method=method,
        )

        # 11. Clean up pending ticket (terminal consume — exactly once)
        await self.pending_totp_store.delete_ticket(cmd.ticket_id)

        # 12. Check backup code low-stock warning (US-26)
        backup_codes_warning: int | None = None
        if method == "backup_code":
            remaining = await self.backup_code_repo.count_unused_for_user(user.id)
            if remaining <= _BACKUP_LOW_THRESHOLD:
                backup_codes_warning = remaining

        return LoginSuccessDTO(
            access_token=session_result.access_token,
            refresh_token=session_result.refresh_token,
            backup_codes_warning=backup_codes_warning,
        )
