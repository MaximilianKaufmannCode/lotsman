// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useHistory — TanStack Query hooks for document and asset audit history.
 *
 * History is fetched from audit-service via the BFF aggregation endpoint.
 * On audit-service unavailability, the query will error — the DrawerHistoryTab
 * shows a retry button in that case.
 */

import { useQuery } from "@tanstack/react-query";
import { getAssetHistory, getDocumentHistory } from "../api";
import type { AuditEvent } from "../types";

const HISTORY_QUERY_KEY = "history" as const;

/**
 * v1.25.1 — staleTime=0 + refetchOnMount: "always".
 *
 * Background: outbox dispatcher runs every 5s, then audit-consumer writes to
 * audit DB. End-to-end propagation from PATCH to "available in /history" is
 * 5–60s. The previous staleTime: 30_000 meant that if the user opened the
 * history tab before the audit consumer finished, TanStack Query cached the
 * empty result for 30s — switching back to the tab returned the cached empty
 * even after events became available.
 *
 * Fix: force fresh fetch every time the History tab mounts. The tab is
 * conditionally rendered (`{activeTab === "history" && <HistoryTab />}`), so
 * unmount/remount means a real refetch. UI also exposes a manual «Обновить»
 * button so the user can re-poll while the audit pipeline catches up.
 */
export function useDocumentHistory(documentId: string | null, limit = 50) {
  return useQuery<AuditEvent[], Error>({
    queryKey: [HISTORY_QUERY_KEY, "document", documentId, limit] as const,
    queryFn: () => {
      if (!documentId) return Promise.resolve([]);
      return getDocumentHistory(documentId, limit);
    },
    enabled: !!documentId,
    staleTime: 0,
    refetchOnMount: "always",
    retry: 1, // One retry on failure; error UI shows retry button for subsequent
  });
}

export function useAssetHistory(assetId: string | null, limit = 50) {
  return useQuery<AuditEvent[], Error>({
    queryKey: [HISTORY_QUERY_KEY, "asset", assetId, limit] as const,
    queryFn: () => {
      if (!assetId) return Promise.resolve([]);
      return getAssetHistory(assetId, limit);
    },
    enabled: !!assetId,
    staleTime: 0,
    refetchOnMount: "always",
    retry: 1,
  });
}

export { HISTORY_QUERY_KEY };
