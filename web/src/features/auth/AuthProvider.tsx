// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * AuthProvider — holds the in-memory access token + decoded claims.
 *
 * Security invariants (ADR-0003, auth-flow-review §6 R-7):
 * - Access token lives in React state ONLY — never localStorage, sessionStorage, IndexedDB.
 * - Refresh cookie is HttpOnly — managed entirely by the BFF (invisible to JS).
 * - On mount: attempt a silent refresh to restore session across page loads.
 *
 * Exports `useAuth()` hook.
 */

import * as React from "react";
import { ApiResponseError, logout as apiLogout, refreshToken, registerTokenAccessor } from "./api";
import { BroadcastCoordinator, TAB_ID } from "./broadcast";
import type {
  AuthStatus,
  BackupCodeVerifyResponse,
  JwtClaims,
  LoginResponse,
  TotpVerifyResponse,
} from "./types";

// ── JWT decode (no verification — BFF has already verified) ──────────────────

function decodeJwtClaims(token: string): JwtClaims | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    // parts[1] is defined — guarded above
    const b64 = (parts[1] as string).replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(b64);
    return JSON.parse(json) as JwtClaims;
  } catch {
    return null;
  }
}

// ── Context ───────────────────────────────────────────────────────────────────

interface AuthContextValue {
  status: AuthStatus;
  accessToken: string | null;
  claims: JwtClaims | null;
  /** Step 1: submit email + password */
  login: (email: string, password: string) => Promise<LoginResponse>;
  /** Step 2a: submit TOTP code */
  completeTotp: (totpSessionToken: string, code: string) => Promise<void>;
  /** Step 2b: submit backup code */
  useBackupCode: (totpSessionToken: string, code: string) => Promise<void>;
  /** Silent token refresh — for external callers (interceptor, scheduler) */
  refresh: () => Promise<string | null>;
  logout: () => Promise<void>;
  /** Called after enrollment to inject token */
  setAccessToken: (token: string) => void;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

// ── Auth state helpers (pure functions, no React deps) ────────────────────────

interface AuthState {
  status: AuthStatus;
  accessToken: string | null;
  claims: JwtClaims | null;
}

const INITIAL_STATE: AuthState = {
  status: "unknown",
  accessToken: null,
  claims: null,
};

type AuthAction =
  | { type: "APPLY_TOKEN"; token: string; claims: JwtClaims }
  | { type: "APPLY_OPAQUE_TOKEN"; token: string }
  | { type: "CLEAR_TOKEN"; status?: AuthStatus }
  | { type: "SET_STATUS"; status: AuthStatus };

function authReducer(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case "APPLY_TOKEN":
      return {
        status: "authenticated",
        accessToken: action.token,
        claims: action.claims,
      };
    case "APPLY_OPAQUE_TOKEN":
      // For non-JWT bearer tokens (enrollment_token = secrets.token_urlsafe(32)
      // per ADR-0008 §D1). Keep accessToken populated so apiFetch's `auth:true`
      // injects the Authorization header. claims stays null (no JWT to decode);
      // status is set separately by the caller (e.g. "first-login-required").
      return {
        ...state,
        accessToken: action.token,
        claims: null,
      };
    case "CLEAR_TOKEN":
      return {
        status: action.status ?? "unauthenticated",
        accessToken: null,
        claims: null,
      };
    case "SET_STATUS":
      return { ...state, status: action.status };
    default:
      return state;
  }
}

// ── Provider ──────────────────────────────────────────────────────────────────

interface AuthProviderProps {
  children: React.ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [state, dispatch] = React.useReducer(authReducer, INITIAL_STATE);

  // Keep a stable ref to the current token for the accessor callback
  const tokenRef = React.useRef<string | null>(null);

  // BroadcastChannel coordinator — created once per tab
  const coordinatorRef = React.useRef<BroadcastCoordinator | null>(null);

  // Stable helpers via ref to avoid exhaustive-deps issues
  const applyToken = React.useCallback((token: string): void => {
    const decoded = decodeJwtClaims(token);
    if (!decoded) {
      // Opaque (non-JWT) bearer — the enrollment_token from /login's
      // first-login branch is `secrets.token_urlsafe(32)` per ADR-0008 §D1
      // (cannot be a full access JWT for security: would grant access-gated
      // endpoints to an un-enrolled user; access JWT requires `sid` which
      // has no session pre-MFA). Store it so apiFetch's `auth:true` injects
      // the Authorization header for the three enrollment routes. The caller
      // sets status (typically "first-login-required") separately.
      tokenRef.current = token;
      dispatch({ type: "APPLY_OPAQUE_TOKEN", token });
      return;
    }
    tokenRef.current = token;
    dispatch({ type: "APPLY_TOKEN", token, claims: decoded });
  }, []);

  const clearToken = React.useCallback((newStatus: AuthStatus = "unauthenticated"): void => {
    tokenRef.current = null;
    dispatch({ type: "CLEAR_TOKEN", status: newStatus });
  }, []);

  // Register the stable accessor (no stale closure — always reads ref)
  React.useEffect(() => {
    registerTokenAccessor(() => tokenRef.current);
  }, []);

  // Open BroadcastChannel on mount, close on unmount
  React.useEffect(() => {
    const coord = new BroadcastCoordinator({
      onTokenRefreshed: (token, _expiresAt) => {
        applyToken(token);
      },
      onLoggedOut: () => {
        clearToken();
      },
    });
    coord.open();
    coordinatorRef.current = coord;
    return () => {
      coord.close();
      coordinatorRef.current = null;
    };
  }, [applyToken, clearToken]);

  // Silent refresh on mount — restores session across page loads
  React.useEffect(() => {
    dispatch({ type: "SET_STATUS", status: "loading" });
    refreshToken()
      .then((res) => {
        applyToken(res.access_token);
      })
      .catch(() => {
        clearToken();
      });
  }, [applyToken, clearToken]);

  // ── Public API ─────────────────────────────────────────────────────────────

  const login = React.useCallback(
    async (email: string, password: string): Promise<LoginResponse> => {
      const { login: apiLogin } = await import("./api");
      dispatch({ type: "SET_STATUS", status: "loading" });
      try {
        const res = await apiLogin(email, password);
        if (res.next_step === "none") {
          applyToken(res.access_token);
        } else if (res.next_step === "verify_totp") {
          dispatch({ type: "SET_STATUS", status: "totp-required" });
        } else if (res.next_step === "enroll_totp") {
          // enrollment_token grants access only to enrollment endpoints
          applyToken(res.enrollment_token);
          dispatch({ type: "SET_STATUS", status: "first-login-required" });
        }
        return res;
      } catch (err) {
        clearToken();
        throw err;
      }
    },
    [applyToken, clearToken],
  );

  const completeTotp = React.useCallback(
    async (totpSessionToken: string, code: string): Promise<void> => {
      const { verifyTotp } = await import("./api");
      dispatch({ type: "SET_STATUS", status: "loading" });
      try {
        const res: TotpVerifyResponse = await verifyTotp(totpSessionToken, code);
        applyToken(res.access_token);
        const expClaims = decodeJwtClaims(res.access_token);
        coordinatorRef.current?.broadcastTokenRefreshed(
          res.access_token,
          (expClaims?.exp ?? 0) * 1000,
        );
      } catch (err) {
        dispatch({ type: "SET_STATUS", status: "totp-required" });
        throw err;
      }
    },
    [applyToken],
  );

  const useBackupCode = React.useCallback(
    async (totpSessionToken: string, code: string): Promise<void> => {
      const { verifyBackupCode } = await import("./api");
      dispatch({ type: "SET_STATUS", status: "loading" });
      try {
        const res: BackupCodeVerifyResponse = await verifyBackupCode(totpSessionToken, code);
        applyToken(res.access_token);
        const expClaims = decodeJwtClaims(res.access_token);
        coordinatorRef.current?.broadcastTokenRefreshed(
          res.access_token,
          (expClaims?.exp ?? 0) * 1000,
        );
      } catch (err) {
        dispatch({ type: "SET_STATUS", status: "totp-required" });
        throw err;
      }
    },
    [applyToken],
  );

  const refresh = React.useCallback(async (): Promise<string | null> => {
    try {
      const isLeader = await coordinatorRef.current?.electLeader();
      if (!isLeader) {
        // Another tab will broadcast the new token
        return null;
      }
      const res = await refreshToken();
      applyToken(res.access_token);
      const expClaims = decodeJwtClaims(res.access_token);
      coordinatorRef.current?.broadcastTokenRefreshed(
        res.access_token,
        (expClaims?.exp ?? 0) * 1000,
      );
      return res.access_token;
    } catch (err) {
      if (err instanceof ApiResponseError && err.status === 401) {
        clearToken();
        coordinatorRef.current?.broadcastLoggedOut();
      }
      return null;
    }
  }, [applyToken, clearToken]);

  const logout = React.useCallback(async (): Promise<void> => {
    try {
      await apiLogout();
    } catch {
      // Swallow — clear local state regardless
    }
    clearToken();
    coordinatorRef.current?.broadcastLoggedOut();
  }, [clearToken]);

  const setAccessToken = React.useCallback(
    (token: string): void => {
      applyToken(token);
    },
    [applyToken],
  );

  const value = React.useMemo<AuthContextValue>(
    () => ({
      status: state.status,
      accessToken: state.accessToken,
      claims: state.claims,
      login,
      completeTotp,
      useBackupCode,
      refresh,
      logout,
      setAccessToken,
    }),
    [state, login, completeTotp, useBackupCode, refresh, logout, setAccessToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within <AuthProvider>");
  }
  return ctx;
}

export { TAB_ID };
