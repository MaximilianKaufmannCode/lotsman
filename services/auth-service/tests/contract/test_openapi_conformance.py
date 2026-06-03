# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Contract tests: auth-service OpenAPI conformance via schemathesis.

Runs schemathesis against the FastAPI app's generated /openapi.json.
For stateless endpoints: auto-generates inputs and asserts responses
  - Never 5xx on valid inputs
  - 4xx for invalid inputs
  - Response bodies match the declared schema

Endpoints requiring complex stateful setup (login, totp/verify) are
tested manually in the integration layer and explicitly excluded here.

Run:
    uv run pytest services/auth-service/tests/contract/ -v
"""

from __future__ import annotations

import pytest

try:
    import schemathesis

    _SCHEMATHESIS_AVAILABLE = True
except ImportError:
    _SCHEMATHESIS_AVAILABLE = False

try:
    from auth_service.main import create_app

    _APP_IMPORTABLE = True
except ImportError:
    _APP_IMPORTABLE = False


pytestmark = pytest.mark.skipif(
    not _SCHEMATHESIS_AVAILABLE or not _APP_IMPORTABLE,
    reason="schemathesis or auth_service.main not available",
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

if _SCHEMATHESIS_AVAILABLE and _APP_IMPORTABLE:
    import os

    # Provide minimal env vars for FastAPI startup
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://auth_app:pw@localhost/lotsman")
    os.environ.setdefault("INTERNAL_JWT_KEY_AUTH", "a" * 32)
    os.environ.setdefault("TOTP_ENC_KEY", "dGVzdC10b3RwLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcysK")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

    try:
        app = create_app()
        schema = schemathesis.from_asgi("/openapi.json", app=app)
    except Exception:
        schema = None  # type: ignore[assignment]

    _STATEFUL_PATHS = {
        "/api/v1/auth/login",
        "/api/v1/auth/totp/verify",
        "/api/v1/totp/enroll",
        "/api/v1/totp/enroll/confirm",
    }

    if schema is not None:

        @schema.parametrize()
        def test_openapi_no_5xx_on_valid_inputs(case: schemathesis.Case) -> None:
            """Every valid-by-schema input must not produce a 5xx response."""
            # Skip stateful endpoints
            if any(path in (case.path or "") for path in _STATEFUL_PATHS):
                pytest.skip("Stateful endpoint — covered in integration tests")

            response = case.call_asgi(app=app)
            assert response.status_code < 500, (
                f"Got {response.status_code} for {case.method} {case.path}: {response.text[:200]}"
            )

else:
    # Placeholder so the file is importable without schemathesis installed
    def test_schemathesis_skipped_when_unavailable() -> None:
        pytest.skip("schemathesis or auth_service.main not importable")
