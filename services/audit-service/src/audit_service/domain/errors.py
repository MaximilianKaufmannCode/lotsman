# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Typed domain errors for audit-service."""

from __future__ import annotations

from lotsman_shared.errors import DomainError


class AuditDomainError(DomainError):
    """Base for all audit-service domain errors."""


class AuditEventNotFoundError(AuditDomainError):
    status_code = 404
    default_message = "Audit event not found"


class DuplicateEventError(AuditDomainError):
    """Raised when idempotency check detects an already-processed envelope id."""

    status_code = 409
    default_message = "Event already processed"
