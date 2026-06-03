# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ChangePassword use case — US-6.

Requires re-MFA for normal path. For the forced enrollment path (must_change_password=True),
re-MFA is not required (the OOB OTP already served as the authentication factor).

Emits PasswordChanged. Also revokes all OTHER sessions.
If must_change_password was True, also emits UserActivated.

ADR-0008 rev. 3 changes (D5.4.1 / D6 / MF-2):
- The forced-enrollment path now calls the shared IssueSession collaborator instead
  of inline session-minting.
- This INTENTIONALLY ADDS ``auth.user.logged_in.v1`` + ``last_login_at`` to the
  forced path (that path emitted neither today — auditing improvement, NOT a
  regression — see ADR-0008 D5.4.1 / INV-6).
- ``user_id`` on the forced path comes from the enrollment ticket resolver (MF-5);
  the command carries it via ``ChangePasswordCommand.user_id`` as before.
- ``session_id`` is unused on the forced path (no pre-existing session); callers
  may pass ``uuid.UUID(int=0)`` as a sentinel.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import ChangePasswordCommand, LoginSuccessDTO
from auth_service.application.policies.password_policy import validate_password
from auth_service.application.ports import (
    BreachedPasswordChecker,
    EventOutbox,
    JwtIssuer,
    LoginAttemptRepository,
    PasswordHasher,
    RedisPendingTotpLoginStore,
    RedisReMfaStore,
    SessionRepository,
    UserRepository,
)
from auth_service.application.use_cases.issue_session import IssueSession
from auth_service.domain.errors import ReMfaRequiredError, UserNotFoundError
from auth_service.domain.events import PasswordChanged, SessionRevoked, UserActivated

_DEFAULT_SESSION_TTL_SECONDS = 43200  # 12 hours


@dataclass(slots=True)
class ChangePassword:
    user_repo: UserRepository
    session_repo: SessionRepository
    hasher: PasswordHasher
    hibp_checker: BreachedPasswordChecker
    re_mfa_store: RedisReMfaStore
    jwt_issuer: JwtIssuer
    outbox: EventOutbox
    attempts_repo: LoginAttemptRepository | None = None
    # Only needed on the forced-enrollment terminal path to delete the ticket.
    pending_totp_store: RedisPendingTotpLoginStore | None = None
    session_ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS

    async def execute(
        self,
        *,
        cmd: ChangePasswordCommand,
        enrollment_token: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> LoginSuccessDTO | None:
        """Change a user's password.

        Returns LoginSuccessDTO on the forced-enrollment path (must_change_password=True),
        None on the normal self-service path.

        Parameters
        ----------
        cmd:
            The command DTO.  ``user_id`` is the resolved actor (from the enrollment
            ticket on the forced path, from the actor JWT on the normal path).
        enrollment_token:
            The opaque enrollment ticket.  Required on the forced-enrollment path
            so the terminal step can delete the ticket.  None on the normal path.
        ip_address / user_agent:
            Forwarded for session creation and audit.
        """
        user = await self.user_repo.get_by_id(cmd.user_id)
        if user is None:
            raise UserNotFoundError()

        forced_path = user.must_change_password

        # Re-MFA gate (only for non-forced path)
        if not forced_path:
            verified = await self.re_mfa_store.is_verified(cmd.user_id, cmd.session_id)
            if not verified:
                raise ReMfaRequiredError()

        # Validate new password against policy
        validate_password(cmd.new_password, hibp_checker=self.hibp_checker)

        # Hash and store new password
        user.password_hash = self.hasher.hash(cmd.new_password)

        if forced_path:
            user.must_change_password = False

        await self.user_repo.update(user)

        # Revoke all OTHER sessions (keep current one on normal path).
        # On the forced path there is no current session yet; ``cmd.session_id``
        # is a sentinel (uuid.UUID(int=0)) and revoke_all_except does nothing
        # meaningful (no sessions exist yet), which is correct.
        revoked_count = await self.session_repo.revoke_all_except(
            user.id, except_session_id=cmd.session_id
        )
        for _ in range(revoked_count):
            await self.outbox.publish(
                SessionRevoked(actor_id=user.id, session_id=cmd.session_id).as_envelope()
            )

        # Emit PasswordChanged
        await self.outbox.publish(PasswordChanged(actor_id=user.id).as_envelope())

        if forced_path:
            # Emit UserActivated (forced enrollment complete)
            await self.outbox.publish(
                UserActivated(actor_id=user.id, user_id=user.id).as_envelope()
            )

            # ADR-0008 D5.4.1 / D6 / MF-2 / INV-6:
            # Issue session via the shared IssueSession collaborator.
            # This INTENTIONALLY ADDS auth.user.logged_in.v1 + last_login_at
            # to the forced-password-change path (that path emitted neither today).
            # This is a deliberate, audited audit-completeness improvement — NOT a
            # regression (QA MUST update the existing ChangePassword outbox
            # tests to expect the added LoggedIn event — see INV-6).
            if self.attempts_repo is None:
                raise RuntimeError(
                    "attempts_repo is required for the forced-enrollment path "
                    "(needed by IssueSession for lockout re-check and audit)."
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

            # Terminal consume: delete the enrollment ticket (exactly once — D5.4.9).
            if enrollment_token is not None and self.pending_totp_store is not None:
                await self.pending_totp_store.delete_ticket(enrollment_token)
                await self.pending_totp_store.delete_confirm_attempts(enrollment_token)

            return LoginSuccessDTO(
                access_token=session_result.access_token,
                refresh_token=session_result.refresh_token,
            )

        return None
