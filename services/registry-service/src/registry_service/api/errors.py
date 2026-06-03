# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Exception handler registration for registry-service.

Maps domain errors to HTTP responses. Use cases never import HTTP status codes.
"""

from __future__ import annotations

from fastapi import FastAPI
from lotsman_shared.errors import register_exception_handlers as _register_shared


def register_exception_handlers(app: FastAPI) -> None:
    """Register all domain error → HTTP JSON response handlers."""
    # The shared kernel handler covers all DomainError subclasses via status_code
    _register_shared(app)
