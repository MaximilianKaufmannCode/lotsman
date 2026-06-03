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
 *   <RoleGuard role="admin">
 *     <AdminUsersPage />
 *   </RoleGuard>
 */

import type * as React from "react";
import { useAuth } from "./AuthProvider";
import type { UserRole } from "./types";

interface RoleGuardProps {
  role: UserRole;
  children: React.ReactNode;
  /** Optional fallback rendered when role does not match */
  fallback?: React.ReactNode;
}

export function RoleGuard({ role, children, fallback = null }: RoleGuardProps) {
  const { claims } = useAuth();

  if (!claims) return null;
  if (claims.role !== role) return <>{fallback}</>;

  return <>{children}</>;
}
