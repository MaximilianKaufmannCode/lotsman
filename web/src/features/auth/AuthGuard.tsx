// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * AuthGuard — wraps private routes.
 *
 * Behavior per ADR-0003 §14 + requirements/auth.md:
 * - status unknown/loading → render skeleton
 * - status unauthenticated → redirect to /login
 * - status first-login-required → redirect to /first-login
 * - status authenticated | totp-required → render children
 *   (totp-required is handled by LoginPage's step machine; if navigating
 *    directly to a private route, unauthenticated is the right redirect)
 */

import { useLocation } from "@tanstack/react-router";
import type * as React from "react";
import { Skeleton } from "@/shared/ui/skeleton";
import { useAuth } from "./AuthProvider";

interface AuthGuardProps {
  children: React.ReactNode;
}

export function AuthGuard({ children }: AuthGuardProps) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "unknown" || status === "loading") {
    return <AuthLoadingSkeleton />;
  }

  if (status === "unauthenticated" || status === "totp-required") {
    // Use hard redirect to avoid TanStack Router's strict search param types.
    // The `next` param is picked up by LoginPage via useSearch.
    const nextPath = location.pathname !== "/login" ? location.pathname : "";
    const redirectUrl = nextPath ? `/login?next=${encodeURIComponent(nextPath)}` : "/login";
    // Use a side-effect-safe redirect on the first render
    window.location.replace(redirectUrl);
    return null;
  }

  if (status === "first-login-required") {
    window.location.replace("/first-login");
    return null;
  }

  // authenticated
  return <>{children}</>;
}

function AuthLoadingSkeleton() {
  return (
    <div
      className="flex min-h-screen flex-col"
      aria-busy="true"
      role="status"
      aria-label="Загрузка..."
    >
      {/* Header skeleton */}
      <div className="sticky top-0 h-14 border-b bg-background/95 px-4 flex items-center gap-4">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-8 flex-1 max-w-sm mx-auto" />
        <Skeleton className="h-9 w-9 rounded-full ml-auto" />
      </div>
      {/* Content skeleton */}
      <div className="flex-1 p-6 space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    </div>
  );
}
