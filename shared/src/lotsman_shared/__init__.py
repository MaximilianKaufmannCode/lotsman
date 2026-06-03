# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Lotsman shared kernel package.

Exposes only the minimum cross-service primitives per ADR-0002 §4:
- System actor UUID constants (actors.py)
- Canonical event envelope (envelope.py)
- Internal JWT issue/verify (internal_jwt.py)
- Structured logging configurator (logging.py)
- Request-id ASGI middleware (middleware.py)
- Health/readiness router factory (health.py)
- Prometheus metrics router factory (metrics.py)
- Base domain error + exception handler factory (errors.py)
"""

__version__ = "0.1.0"
