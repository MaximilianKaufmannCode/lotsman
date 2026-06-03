// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Auth feature — shared TypeScript types.
 * All types derive from ADR-0003 §7 (external JWT claims) and §8 (sessions).
 */

export type UserRole = "admin" | "editor" | "viewer" | "super_admin";

/** Decoded claims from the RS256 access JWT. Never persisted. */
export interface JwtClaims {
  /** user UUID */
  sub: string;
  email: string;
  role: UserRole;
  /** session UUID — links to auth.sessions.id */
  sid: string;
  /** JWT ID — UUIDv4, for replay prevention */
  jti: string;
  iss: string;
  aud: string;
  iat: number;
  exp: number;
  nbf: number;
}

export type AuthStatus =
  | "unknown"
  | "loading"
  | "authenticated"
  | "totp-required"
  | "first-login-required"
  | "unauthenticated";

/** Response from POST /api/v1/auth/login when password is correct and TOTP enrolled */
export interface LoginTotpRequiredResponse {
  next_step: "verify_totp";
  totp_session_token: string;
}

/** Response from POST /api/v1/auth/login when first-time user */
export interface LoginFirstLoginResponse {
  next_step: "enroll_totp";
  enrollment_token: string;
}

/** Response from POST /api/v1/auth/login on full success */
export interface LoginSuccessResponse {
  next_step: "none";
  access_token: string;
  token_type: "Bearer";
}

export type LoginResponse =
  | LoginTotpRequiredResponse
  | LoginFirstLoginResponse
  | LoginSuccessResponse;

/** Response from POST /api/v1/auth/totp/verify */
export interface TotpVerifyResponse {
  access_token: string;
  token_type: "Bearer";
}

/** Response from POST /api/v1/auth/backup-codes/verify */
export interface BackupCodeVerifyResponse {
  access_token: string;
  token_type: "Bearer";
}

/** Response from POST /api/v1/auth/refresh */
export interface RefreshResponse {
  access_token: string;
  token_type: "Bearer";
}

/** Response from POST /api/v1/auth/totp/enroll */
export interface TotpEnrollResponse {
  secret_b32: string;
  otpauth_url: string;
}

/** Response from POST /api/v1/auth/backup-codes/regenerate */
export interface BackupCodesRegenerateResponse {
  codes: string[];
  generated_at: string;
}

/** Session object from GET /api/v1/auth/sessions/me */
export interface SessionItem {
  id: string;
  user_agent: string;
  ip_address: string;
  created_at: string;
  last_seen_at: string;
  is_current: boolean;
}

/** User object for admin user management */
export interface AdminUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  is_locked: boolean;
  totp_enrolled: boolean;
  last_login_at: string | null;
  created_at: string;
}

/** Detailed user object from GET /api/v1/admin/users/{id} — extends AdminUser */
export interface AdminUserDetail extends AdminUser {
  must_change_password: boolean;
  updated_at: string;
}

/** Active session object from GET /api/v1/admin/users/{id}/sessions */
export interface AdminUserSession {
  id: string;
  user_id: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  user_agent?: string | null;
  ip_address?: string | null;
}

/** Response from admin POST /api/v1/admin/users */
export interface CreateUserResponse {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  oob_otp: string;
}

/** Re-MFA token response from POST /api/v1/auth/re-mfa */
export interface ReMfaResponse {
  re_mfa_token: string;
}

/** Generic API error shape */
export interface ApiError {
  detail: string;
  code?: string;
}

/**
 * Self-service profile — returned by GET /api/v1/auth/me
 * and PATCH /api/v1/auth/me.
 *
 * email is read-only from the user's perspective (whitelist-controlled identity).
 * full_name is editable by the user.
 */
export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  must_change_password: boolean;
  totp_enrolled: boolean;
  is_locked: boolean;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
}
