# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Exception handler registration for audit-service."""

from __future__ import annotations

from fastapi import FastAPI
from lotsman_shared.errors import register_exception_handlers as _register


def register_exception_handlers(app: FastAPI) -> None:
    _register(app)
