# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Hardcoded whitelist of allowed services and operations for system-control.

SECURITY: These sets are CONSTANTS defined at module level and imported read-only.
They are never populated from user input, environment variables, or the database.
Any attempt to bypass the whitelist by providing a service name not in this set
will be rejected by the API layer before any Docker operation is attempted.

Adding a new service requires a code change + code review. This is intentional.
"""

from __future__ import annotations

# Docker container names that system-control is allowed to restart or exec into.
# These are the Compose `container_name` values (not service names).
ALLOWED_SERVICES: frozenset[str] = frozenset(
    {
        "lotsman_auth_svc",
        "lotsman_registry_svc",
        "lotsman_notification_svc",
        "lotsman_audit_svc",
        "lotsman_web_bff",
    }
)

# Friendly service→container_name mapping for use in API request bodies.
# Users submit the short name; we resolve to the canonical container name.
SERVICE_TO_CONTAINER: dict[str, str] = {
    "auth-svc": "lotsman_auth_svc",
    "registry-svc": "lotsman_registry_svc",
    "notification-svc": "lotsman_notification_svc",
    "audit-svc": "lotsman_audit_svc",
    "web-bff": "lotsman_web_bff",
}

# Allowed short service names (keys of SERVICE_TO_CONTAINER) — for validation.
ALLOWED_SERVICE_NAMES: frozenset[str] = frozenset(SERVICE_TO_CONTAINER.keys())

# The Alembic command executed inside a container via exec_run.
# Hardcoded — never interpolated from user input.
ALEMBIC_UPGRADE_CMD: tuple[str, ...] = ("alembic", "upgrade", "head")

# Workdir inside the service containers where alembic.ini lives.
ALEMBIC_WORKDIR: str = "/app"

# Maximum lines that can be requested from docker logs (defence against DoS).
MAX_LOG_TAIL: int = 500
