# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ADR-0008 BFF-side structural tests (T7 / MF-5).

Verifies that the 3 enrollment BFF handlers do NOT use RequireAccessClaims
(they operate on the anonymous ticket lane, same as verify_totp).

Also verifies the _extract_enrollment_token helper reads from BODY (primary)
with Authorization: Bearer fallback (D7 SPA compat).
"""

from __future__ import annotations

import inspect
from typing import Any

# ---------------------------------------------------------------------------
# T7 / MF-5: No RequireAccessClaims on the 3 BFF enrollment handlers
# ---------------------------------------------------------------------------


def _get_bff_auth_handler(name: str) -> Any:
    """Return a handler from web_bff.api.v1.auth or skip if not importable."""
    import pytest

    try:
        import web_bff.api.v1.auth as bff_auth_module
    except ImportError as e:
        pytest.skip(f"web_bff.api.v1.auth not importable: {e}")

    handler = getattr(bff_auth_module, name, None)
    if handler is None:
        pytest.fail(f"Handler '{name}' not found in web_bff.api.v1.auth")
    return handler


def _get_param_annotation_strings(func: Any) -> dict[str, str]:
    """Return {param_name: str(annotation)} for all parameters."""
    return {
        name: str(param.annotation) for name, param in inspect.signature(func).parameters.items()
    }


def test_t7_bff_enroll_totp_has_no_require_access_claims() -> None:
    """T7/MF-5: BFF enroll_totp handler must NOT declare RequireAccessClaims.

    This handler is in the anonymous ticket lane (ADR-0008 D2/D3).
    RequireAccessClaims would reject the opaque enrollment token (the root bug).
    """
    handler = _get_bff_auth_handler("enroll_totp")
    params = _get_param_annotation_strings(handler)

    for param_name, annotation_str in params.items():
        assert "RequireAccessClaims" not in annotation_str, (
            f"T7/MF-5: BFF enroll_totp parameter '{param_name}' has annotation "
            f"'{annotation_str}' — RequireAccessClaims MUST NOT be declared "
            "on enrollment routes (ADR-0008 D2/D3 anonymous ticket lane)"
        )


def test_t7_bff_confirm_totp_enrollment_has_no_require_access_claims() -> None:
    """T7/MF-5: BFF confirm_totp_enrollment handler must NOT declare RequireAccessClaims."""
    handler = _get_bff_auth_handler("confirm_totp_enrollment")
    params = _get_param_annotation_strings(handler)

    for param_name, annotation_str in params.items():
        assert "RequireAccessClaims" not in annotation_str, (
            f"T7/MF-5: BFF confirm_totp_enrollment parameter '{param_name}' "
            f"annotation '{annotation_str}' — RequireAccessClaims MUST NOT be present"
        )


def test_t7_bff_change_password_enrollment_branch_uses_ticket_client_method() -> None:
    """T7/MF-5: BFF change_password uses change_password_with_ticket for enrollment lane.

    On the enrollment branch the BFF calls auth_client.change_password_with_ticket
    (anonymous lane, _ANON_ACTOR_ID) not any actor-JWT-bound method.
    The lane selection uses _extract_password_change_credential (F-N-1 fix —
    route-local helper that tries JWT decode before falling back to opaque-ticket
    so a normal access JWT is never misrouted into the ticket lane).
    """
    handler = _get_bff_auth_handler("change_password")
    source = inspect.getsource(handler)

    assert "change_password_with_ticket" in source, (
        "T7/MF-5: BFF change_password must call auth_client.change_password_with_ticket "
        "for the enrollment ticket lane (ADR-0008 D3 / MF-5)"
    )
    assert "_extract_password_change_credential" in source, (
        "F-N-1: BFF change_password must use _extract_password_change_credential "
        "(route-local helper) not _extract_enrollment_token directly — the helper "
        "tries JWT decode before opaque-ticket fallback to avoid mis-routing "
        "a normal access JWT into the ticket lane"
    )


# ---------------------------------------------------------------------------
# T7 / D7: _extract_enrollment_token helper — body primary, Auth header fallback
# ---------------------------------------------------------------------------


def test_t7_extract_enrollment_token_reads_from_body_primary() -> None:
    """T7/D7: _extract_enrollment_token prefers body field over Authorization header."""
    try:
        from web_bff.api.v1.auth import _extract_enrollment_token
    except ImportError as e:
        import pytest

        pytest.skip(f"web_bff not importable: {e}")

    body = {"enrollment_token": "body_token_value", "other": "field"}
    result = _extract_enrollment_token(body, authorization="Bearer header_token_value")
    assert result == "body_token_value", (
        "T7/D7: body field enrollment_token must take precedence over Authorization header"
    )


def test_t7_extract_enrollment_token_falls_back_to_authorization_header() -> None:
    """T7/D7: _extract_enrollment_token falls back to Authorization Bearer when body missing."""
    try:
        from web_bff.api.v1.auth import _extract_enrollment_token
    except ImportError as e:
        import pytest

        pytest.skip(f"web_bff not importable: {e}")

    body: dict = {}  # no enrollment_token in body
    result = _extract_enrollment_token(body, authorization="Bearer opaque_fallback_token")
    assert result == "opaque_fallback_token", (
        "T7/D7: should fall back to Authorization Bearer when enrollment_token absent from body"
    )


def test_t7_extract_enrollment_token_returns_none_when_both_missing() -> None:
    """T7/D7: _extract_enrollment_token returns None when neither body nor header has token."""
    try:
        from web_bff.api.v1.auth import _extract_enrollment_token
    except ImportError as e:
        import pytest

        pytest.skip(f"web_bff not importable: {e}")

    result = _extract_enrollment_token({}, authorization=None)
    assert result is None, (
        "T7/D7: must return None when enrollment_token absent from both body and header"
    )
