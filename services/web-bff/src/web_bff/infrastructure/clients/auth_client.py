# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Async HTTP client for auth-service.

All public methods accept `actor_id` and `role` (for internal JWT minting)
plus any endpoint-specific arguments, and return parsed dicts or raw httpx.Response.

Unauthenticated BFF→auth calls (login, totp/verify, etc.) use the
ACTOR_OUTBOX_DISPATCHER system actor — auth-service does not validate the
internal-JWT sub for unprotected endpoints; it only verifies the signature.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from lotsman_shared.actors import ACTOR_OUTBOX_DISPATCHER

from web_bff.infrastructure.clients.base import DownstreamClient

# System actor used for internal JWTs on unauthenticated BFF→auth forwarding.
# The auth-service does not validate the sub on unprotected endpoints.
_ANON_ACTOR_ID: uuid.UUID = ACTOR_OUTBOX_DISPATCHER
_ANON_ROLE: str = "system"


class AuthClient(DownstreamClient):
    """Client for auth-service endpoints.

    Audience: 'auth-service' (per ADR-0003 §10).
    """

    AUDIENCE = "auth-service"

    # ------------------------------------------------------------------ #
    # Unauthenticated flows (no Bearer token from the SPA)
    # ------------------------------------------------------------------ #

    async def lookup_users(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        user_ids: list[uuid.UUID],
    ) -> httpx.Response:
        """Bulk-lookup user full_name/email by IDs (internal endpoint).

        Used by registry document-history enrichment in web-bff to resolve
        actor_id and `responsible_user_id` UUIDs into ФИО for the UI.
        """
        return await self.post(
            "/api/v1/internal/users/lookup",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"ids": [str(i) for i in user_ids]},
        )

    async def login(
        self,
        *,
        email: str,
        password: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/login — password phase."""
        return await self.post(
            "/api/v1/auth/login",
            actor_id=_ANON_ACTOR_ID,
            role=_ANON_ROLE,
            request_id=request_id,
            json={"email": email, "password": password},
        )

    async def verify_totp(
        self,
        *,
        session_ticket: str,
        totp_code: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/totp/verify — TOTP phase."""
        return await self.post(
            "/api/v1/auth/totp/verify",
            actor_id=_ANON_ACTOR_ID,
            role=_ANON_ROLE,
            request_id=request_id,
            json={"session_ticket": session_ticket, "totp_code": totp_code},
        )

    async def verify_totp_backup_code(
        self,
        *,
        session_ticket: str,
        backup_code: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/totp/verify — backup-code path.

        Auth-service's verify_totp endpoint doubles as the backup-code path
        when backup_code is provided in the body.
        """
        return await self.post(
            "/api/v1/auth/totp/verify",
            actor_id=_ANON_ACTOR_ID,
            role=_ANON_ROLE,
            request_id=request_id,
            json={"session_ticket": session_ticket, "backup_code": backup_code},
        )

    async def refresh_tokens(
        self,
        *,
        refresh_token: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/refresh — rotate refresh token.

        The refresh token is forwarded as a cookie so auth-service reads it
        via its own cookie logic.
        """
        headers = self._headers(
            actor_id=_ANON_ACTOR_ID,
            role=_ANON_ROLE,
            request_id=request_id,
        )
        return await self._client.post(
            "/api/v1/auth/refresh",
            headers=headers,
            cookies={"refresh": refresh_token},
        )

    # ------------------------------------------------------------------ #
    # Authenticated flows (actor from decoded Bearer JWT)
    # ------------------------------------------------------------------ #

    async def enroll_totp(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/totp/enroll — initiate TOTP enrollment (normal authenticated path)."""
        return await self.post(
            "/api/v1/auth/totp/enroll",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def enroll_totp_with_ticket(
        self,
        *,
        enrollment_token: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/totp/enroll — enrollment-ticket lane (ADR-0008 D3 / MF-5).

        Uses _ANON_ACTOR_ID exactly as verify_totp does — the auth-service resolves
        identity from the enrollment_token body field, not the internal JWT sub.
        MF-3/D3a.1: enrollment_token MUST NOT be logged.
        """
        return await self.post(
            "/api/v1/auth/totp/enroll",
            actor_id=_ANON_ACTOR_ID,
            role=_ANON_ROLE,
            request_id=request_id,
            json={"enrollment_token": enrollment_token},
        )

    async def confirm_totp_enrollment(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        code: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/totp/enroll/confirm — confirm enrollment code (normal path)."""
        return await self.post(
            "/api/v1/auth/totp/enroll/confirm",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"code": code},
        )

    async def confirm_totp_enrollment_with_ticket(
        self,
        *,
        enrollment_token: str,
        code: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/totp/enroll/confirm — enrollment-ticket lane (ADR-0008 D3).

        MF-3/D3a.1: enrollment_token and code MUST NOT be logged.
        """
        return await self.post(
            "/api/v1/auth/totp/enroll/confirm",
            actor_id=_ANON_ACTOR_ID,
            role=_ANON_ROLE,
            request_id=request_id,
            json={"enrollment_token": enrollment_token, "code": code},
        )

    async def logout(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        refresh_token: str | None,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/logout — revoke current session."""
        headers = self._headers(actor_id=actor_id, role=role, request_id=request_id)
        cookies: dict[str, str] = {}
        if refresh_token:
            cookies["refresh"] = refresh_token
        return await self._client.post(
            "/api/v1/auth/logout",
            headers=headers,
            cookies=cookies,
        )

    async def regenerate_backup_codes(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/backup-codes/regenerate — regenerate all backup codes."""
        return await self.post(
            "/api/v1/auth/backup-codes/regenerate",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def re_mfa_check(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        totp_code: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/mfa-check — verify TOTP for sensitive operations."""
        return await self.post(
            "/api/v1/auth/mfa-check",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"totp_code": totp_code},
        )

    async def change_password(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        new_password: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/change-password — normal authenticated path."""
        return await self.post(
            "/api/v1/auth/change-password",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"new_password": new_password},
        )

    async def change_password_with_ticket(
        self,
        *,
        enrollment_token: str,
        new_password: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/change-password — forced-enrollment terminal step (ADR-0008 D3).

        Uses _ANON_ACTOR_ID — auth-service resolves identity from enrollment_token body field.
        MF-3/D3a.1: enrollment_token and new_password MUST NOT be logged.
        """
        return await self.post(
            "/api/v1/auth/change-password",
            actor_id=_ANON_ACTOR_ID,
            role=_ANON_ROLE,
            request_id=request_id,
            json={"enrollment_token": enrollment_token, "new_password": new_password},
        )

    async def list_my_sessions(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/auth/sessions — list authenticated user's active sessions."""
        return await self.get(
            "/api/v1/auth/sessions",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def revoke_session(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        session_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """DELETE /api/v1/auth/sessions/{session_id} — revoke a specific session."""
        return await self.delete(
            f"/api/v1/auth/sessions/{session_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    # ------------------------------------------------------------------ #
    # Admin flows
    # ------------------------------------------------------------------ #

    async def admin_list_users(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/admin/users."""
        return await self.get(
            "/api/v1/admin/users",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_get_user(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/admin/users/{user_id}."""
        return await self.get(
            f"/api/v1/admin/users/{user_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_create_user(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        body: dict[str, Any],
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/users."""
        return await self.post(
            "/api/v1/admin/users",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def admin_change_role(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        new_role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """PATCH /api/v1/admin/users/{user_id}/role."""
        return await self.patch(
            f"/api/v1/admin/users/{user_id}/role",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"role": new_role},
        )

    async def admin_lock_user(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/users/{user_id}/lockout."""
        return await self.post(
            f"/api/v1/admin/users/{user_id}/lockout",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_unlock_user(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """DELETE /api/v1/admin/users/{user_id}/lockout."""
        return await self.delete(
            f"/api/v1/admin/users/{user_id}/lockout",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_get_user_sessions(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/admin/users/{user_id}/sessions."""
        return await self.get(
            f"/api/v1/admin/users/{user_id}/sessions",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_revoke_all_sessions(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """DELETE /api/v1/admin/users/{user_id}/sessions."""
        return await self.delete(
            f"/api/v1/admin/users/{user_id}/sessions",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_reset_totp(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        admin_totp_code: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/users/{user_id}/totp/reset."""
        return await self.post(
            f"/api/v1/admin/users/{user_id}/totp/reset",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"admin_totp_code": admin_totp_code},
        )

    async def admin_reset_password(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/users/{user_id}/password/reset."""
        return await self.post(
            f"/api/v1/admin/users/{user_id}/password/reset",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_deactivate_user(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/users/{user_id}/deactivate."""
        return await self.post(
            f"/api/v1/admin/users/{user_id}/deactivate",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_reactivate_user(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/users/{user_id}/reactivate — restore soft-deleted user (US-104)."""
        return await self.post(
            f"/api/v1/admin/users/{user_id}/reactivate",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_delete_user(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """DELETE /api/v1/admin/users/{user_id} — permanent soft-delete."""
        return await self.delete(
            f"/api/v1/admin/users/{user_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_revoke_session(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """DELETE /api/v1/admin/users/{user_id}/sessions/{session_id} — revoke one session (US-105)."""
        return await self.delete(
            f"/api/v1/admin/users/{user_id}/sessions/{session_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_update_user_profile(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        full_name: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """PATCH /api/v1/admin/users/{user_id}/profile — update target user's full_name (US-103)."""
        return await self.patch(
            f"/api/v1/admin/users/{user_id}/profile",
            actor_id=actor_id,
            role=role,
            json={"full_name": full_name},
            request_id=request_id,
        )

    async def admin_re_invite_user(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        body: dict[str, Any],
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/users/{user_id}/invite — re-invite pending user."""
        return await self.post(
            f"/api/v1/admin/users/{user_id}/invite",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    # ------------------------------------------------------------------ #
    # Self-service profile
    # ------------------------------------------------------------------ #

    async def get_my_profile(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/auth/me — fetch authenticated user's profile."""
        return await self.get(
            "/api/v1/auth/me",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def update_my_profile(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        full_name: str,
        ui_font_scale: int | None = None,
        request_id: str | None = None,
    ) -> httpx.Response:
        """PATCH /api/v1/auth/me — update authenticated user's full_name and/or
        UI font-size preference.

        ui_font_scale is forwarded only when provided so a name-only edit never
        touches the preference (and vice-versa); auth-service treats a missing
        field as "leave unchanged".
        """
        payload: dict[str, object] = {"full_name": full_name}
        if ui_font_scale is not None:
            payload["ui_font_scale"] = ui_font_scale
        return await self.patch(
            "/api/v1/auth/me",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=payload,
        )

    async def request_email_change(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        new_email: str,
        totp_code: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/me/change-email/request — initiate email change flow."""
        return await self.post(
            "/api/v1/auth/me/change-email/request",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"new_email": new_email, "totp_code": totp_code},
        )

    async def confirm_email_change(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id_val: str,
        verification_code: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/auth/me/change-email/confirm — confirm email change."""
        return await self.post(
            "/api/v1/auth/me/change-email/confirm",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"request_id": request_id_val, "verification_code": verification_code},
        )

    # ------------------------------------------------------------------ #
    # System — key rotation (super_admin only, proxied from BFF system.py)
    # ------------------------------------------------------------------ #

    async def system_list_key_rotations(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/system/keys — list key rotation records."""
        return await self.get(
            "/api/v1/system/keys",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def system_record_key_rotation(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        key_id: str,
        rotated_at: object,
        note: str | None = None,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/system/keys/{key_id}/rotated — record key rotation."""
        from datetime import datetime as _dt

        rotated_at_str = rotated_at.isoformat() if isinstance(rotated_at, _dt) else str(rotated_at)
        return await self.post(
            f"/api/v1/system/keys/{key_id}/rotated",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"rotated_at": rotated_at_str, "note": note},
        )

    # ------------------------------------------------------------------ #
    # SavedFilters (v1.23.0 — registry-filters feature)
    # ------------------------------------------------------------------ #

    async def list_my_saved_filters(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/auth/me/saved-filters — list user's named filter presets."""
        return await self.get(
            "/api/v1/auth/me/saved-filters",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def create_saved_filter(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
        body: dict[str, Any],
    ) -> httpx.Response:
        """POST /api/v1/auth/me/saved-filters — create a new named filter preset."""
        return await self.post(
            "/api/v1/auth/me/saved-filters",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def update_saved_filter(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        filter_id: uuid.UUID,
        request_id: str | None = None,
        body: dict[str, Any],
    ) -> httpx.Response:
        """PATCH /api/v1/auth/me/saved-filters/{filter_id} — partial update."""
        return await self.patch(
            f"/api/v1/auth/me/saved-filters/{filter_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def delete_saved_filter(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        filter_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """DELETE /api/v1/auth/me/saved-filters/{filter_id} — hard delete."""
        return await self.delete(
            f"/api/v1/auth/me/saved-filters/{filter_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )
