# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""VerifyBackupCode — US-5 (used inside VerifyTotp; standalone for testing).

This module exists as a thin delegation to the backup-code verification logic
inside VerifyTotp. The actual business logic lives there; this module is a
re-export for clarity and test isolation.
"""

# The backup code verification path is implemented directly in verify_totp.py
# (when backup_code is provided). This file is kept for explicit US-5 mapping.
