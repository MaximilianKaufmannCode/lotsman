# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""CompleteFirstLogin use case — US-1 OOB OTP enrollment path.

After the user has:
1. Logged in with OOB OTP (StartLogin returned LoginPendingEnrollDTO)
2. Enrolled TOTP (EnrollTotp + ConfirmTotpEnrollment)
3. Changed password (ChangePassword)

This use case marks must_change_password=False, emits UserActivated,
and issues the final tokens (same as a normal login success).

Called implicitly by the ChangePassword use case on the enrollment path.
"""

from __future__ import annotations

# This is intentionally thin — the real work is split across:
# - EnrollTotp / ConfirmTotpEnrollment (TOTP setup)
# - ChangePassword (password update, which detects must_change_password and emits UserActivated)
# - VerifyTotp path (session + JWT issuance after full enrollment)
#
# CompleteFirstLogin is a marker — the ChangePassword use case detects
# must_change_password=True and emits UserActivated + issues final tokens.
