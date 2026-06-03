// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";
import "@/i18n/index";
import { registerCalendarSubscriptionTokenAccessor } from "@/features/admin/calendar-subscriptions/api";
import { registerChannelTokenAccessor } from "@/features/admin/channels/api";
import { registerDocTypeFieldsTokenAccessor } from "@/features/admin/document-types/custom-fields-api";
import { AuthProvider, useAuth } from "@/features/auth/AuthProvider";
import { registerTokenAccessor as registerAuthTokenAccessor } from "@/features/auth/api";
import { registerRegistryTokenAccessor } from "@/features/registry/api";
import { registerColumnLabelsTokenAccessor } from "@/features/registry/columnLabelsApi";
import { registerColumnOrderTokenAccessor } from "@/features/registry/columnOrderApi";
import { registerSystemTokenAccessor } from "@/features/system/api";
import { registerClientTokenAccessor } from "@/shared/api/client";
import { handle401, registerInterceptorCallbacks } from "@/shared/api/interceptor";

// ── QueryClient with global 401 handling ──────────────────────────────────────

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (failureCount, error) => {
          // Don't retry on 401 — interceptor will handle redirect
          if (error instanceof Error && error.message.includes("401")) return false;
          return failureCount < 1;
        },
      },
    },
  });
}

// Singleton so hot-reload doesn't create duplicate clients
let browserQueryClient: QueryClient | undefined;

function getQueryClient(): QueryClient {
  if (typeof window === "undefined") {
    return makeQueryClient();
  }
  if (!browserQueryClient) {
    browserQueryClient = makeQueryClient();
  }
  return browserQueryClient;
}

// ── Interceptor wiring — must be inside AuthProvider ─────────────────────────

function InterceptorWiring() {
  const { refresh, logout, accessToken } = useAuth();
  const tokenRef = React.useRef<string | null>(null);
  tokenRef.current = accessToken;

  React.useEffect(() => {
    // Register the SAME token-getter into all three fetch surfaces.
    // The closure reads from tokenRef so it always returns the current value.
    const getToken = () => tokenRef.current;
    registerAuthTokenAccessor(getToken);
    registerRegistryTokenAccessor(getToken);
    registerClientTokenAccessor(getToken);
    registerChannelTokenAccessor(getToken);
    registerCalendarSubscriptionTokenAccessor(getToken);
    registerSystemTokenAccessor(getToken);
    registerDocTypeFieldsTokenAccessor(getToken);
    registerColumnOrderTokenAccessor(getToken);
    registerColumnLabelsTokenAccessor(getToken);

    registerInterceptorCallbacks({
      refresh,
      logout,
      navigateToLogin: () => {
        // Hard navigate to login — avoids stale router state after 401
        window.location.href = "/login";
      },
    });
  }, [refresh, logout]);

  return null;
}

// ── Providers ─────────────────────────────────────────────────────────────────

interface ProvidersProps {
  children: React.ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  const queryClient = getQueryClient();

  // Wire global 401 handling into TanStack Query
  React.useEffect(() => {
    queryClient.getQueryCache().config.onError = async (error) => {
      if (error instanceof Error && error.message === "401") {
        await handle401(false);
        queryClient.invalidateQueries();
      }
    };
    queryClient.getMutationCache().config.onError = async (error) => {
      if (error instanceof Error && error.message === "401") {
        await handle401(false);
      }
    };
  }, [queryClient]);

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <InterceptorWiring />
        {children}
      </AuthProvider>
    </QueryClientProvider>
  );
}
