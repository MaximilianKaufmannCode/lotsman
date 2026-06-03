# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ResolveEnrollmentTicket use case — ADR-0008 D3b / MF-5.

Extracts the shared ticket-resolution logic from the API layer into the
application layer where it belongs.  All three enrollment endpoints
(enroll, confirm, change-password ticket-branch) depend on this use case.

Invariants enforced:
  MF-1 / D1.3  — only ENROLL-scoped tickets are accepted.
  MF-4 / D5.3.1 — user must not already have TOTP enrolled (TOTP_SENTINEL check)
                  on /totp/enroll and /totp/enroll/confirm. NOT enforced on the
                  /change-password ticket-branch — see ``allow_totp_enrolled``.
  MF-7          — any failure raises InvalidCredentialsError (uniform 401).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from auth_service.application.ports import RedisPendingTotpLoginStore, UserRepository
from auth_service.domain.entities import TOTP_SENTINEL
from auth_service.domain.errors import InvalidCredentialsError
from auth_service.domain.value_objects import TicketScope


@dataclass(slots=True)
class ResolveEnrollmentTicket:
    """Resolve an enrollment ticket to a user_id with scope and AAL2 re-checks.

    Dependencies:
        pending_totp_store — Redis store for enrollment/login tickets.
        user_repo          — User repository for MF-4 post-resolution DB re-check.
    """

    pending_totp_store: RedisPendingTotpLoginStore
    user_repo: UserRepository

    async def execute(
        self,
        *,
        ticket_id: str,
        allow_totp_enrolled: bool = False,
    ) -> uuid.UUID:
        """Resolve *ticket_id* to the owning user's UUID.

        Parameters
        ----------
        ticket_id:
            The opaque enrollment ticket string from the request body.
        allow_totp_enrolled:
            If False (default — used by /totp/enroll and /totp/enroll/confirm):
            enforce MF-4 — reject if the user already has a non-SENTINEL
            ``totp_secret_enc`` (defends against re-enrolling TOTP via a stale
            ticket; MUST NOT overwrite a live secret).
            If True (used by the forced /change-password ticket-branch ONLY):
            ALLOW user.totp_secret_enc != SENTINEL. By design this branch runs
            AFTER /totp/enroll/confirm has just persisted the TOTP secret as part
            of the first-login enrollment chain (ADR-0008 D5.5). The ChangePassword
            use case independently gates on user.must_change_password == True, so
            an attacker holding the ticket cannot use this relaxation to mutate a
            regular (non-pending) account: ChangePassword's forced-path guard would
            reject. The MF-4 check on /change-password was over-aggressive — it
            blocked the legit Step 3 of the documented enrollment chain. (See
            v1.13.10 post-mortem.)

        Returns
        -------
        uuid.UUID
            The user_id bound to the ticket.

        Raises
        ------
        InvalidCredentialsError
            On any failure: missing/expired ticket, scope mismatch, unknown user,
            or (when allow_totp_enrolled=False) already-enrolled user.  The caller
            never learns *which* check failed (MF-7 / uniform generic 401).
        """
        # D1.3 / MF-1: resolve with expected_scope=ENROLL — LOGIN tickets rejected.
        user_id = await self.pending_totp_store.get_user_id(
            ticket_id, expected_scope=TicketScope.ENROLL
        )
        if user_id is None:
            raise InvalidCredentialsError()

        # D5.3.1 / MF-4: re-check from DB after resolving the ticket.
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise InvalidCredentialsError()
        if not allow_totp_enrolled and user.totp_secret_enc != TOTP_SENTINEL:
            # On /totp/enroll and /totp/enroll/confirm: reject with no mutation
            # (D5.3.2 / MF-4) — protects against re-enrolling over a live secret.
            raise InvalidCredentialsError()

        return user_id
