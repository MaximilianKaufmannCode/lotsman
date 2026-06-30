// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * RoleGuard — UX-only gating for role-restricted UI.
 *
 * Backend is authoritative; this component hides UI from non-admins.
 * Renders null (not a redirect) for mismatched roles — so an editor
 * navigating to /admin/users gets a 403 from the API, not a confusing redirect.
 *
 * Usage:
 *   <RoleGuard role="admin">…</RoleGuard>
 *   <RoleGuard role={["admin", "editor"]}>…</RoleGuard>
 */

import type * as React from "react";
import { useAuth } from "./AuthProvider";
import type { UserRole } from "./types";

interface RoleGuardProps {
  /** A single allowed role, or any-of a list of allowed roles. */
  role: UserRole | UserRole[];
  children: React.ReactNode;
  /** Optional fallback rendered when role does not match */
  fallback?: React.ReactNode;
}

export function RoleGuard({ role, children, fallback = null }: RoleGuardProps) {
  const { claims } = useAuth();

  if (!claims) return null;
  const allowed = Array.isArray(role) ? role : [role];
  if (!allowed.includes(claims.role)) return <>{fallback}</>;

  return <>{children}</>;
}
