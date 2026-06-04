# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Root conftest for the Лоцман monorepo.

Sets environment variables needed by all service Settings classes so that
tests can instantiate Settings and create FastAPI apps without a real database
or Redis connection. Integration tests override these via their own fixtures.

This conftest runs ONCE before any per-service conftest, so per-service
conftest files can remain empty (they exist only as pytest discovery anchors).
"""

from __future__ import annotations

import os

# Required by all backend services
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("INTERNAL_JWT_SECRET", "test-secret-unit-tests-only")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# web-bff fail-closed JWT verification (security remediation 2026-06): unit tests
# run with the dev opt-in so Settings() can be built without a real RS256 key.
# Production leaves this false (default) — see web_bff.config.Settings validator.
os.environ.setdefault("JWT_ALLOW_UNVERIFIED", "true")

# notification-service: Fernet key for channel-config encryption (ADR-0004 §4).
# This is a fixed test-only key and MUST NOT be used in production.
os.environ.setdefault("CHANNEL_ENC_KEY", "WU7sIwVKKiRXzwyhhAbQYniTw0zisc1XnaEaQ1FiwIk=")

# notification-service: internal JWT key used when calling registry-service.
os.environ.setdefault(
    "INTERNAL_JWT_KEY_NOTIFICATION",
    "test-notification-jwt-key-32-bytes-long-for-tests",
)

# system-control sidecar: internal JWT key.
os.environ.setdefault(
    "INTERNAL_JWT_KEY_SYSTEM_CONTROL",
    "test-system-control-jwt-key-32-bytes-long!!!!",
)
