// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useDocuments — TanStack Query hook for the registry document list.
 *
 * Pagination strategy: offset-based. The BFF returns { items, total, page, page_size }.
 * We assume offset pagination rather than cursor-based because the backend
 * needs to support stable sort for Excel-like "go to page 5" behaviour.
 * If backend switches to cursor pagination, update the queryKey and fetch logic here.
 *
 * placeholderData: keeps previous results visible during re-fetch (no loading flash
 * on filter/sort changes, matching the Doherty Threshold target).
 */

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { type ListDocumentsParams, listDocuments } from "../api";
import type { PaginatedDocuments } from "../types";

export const DOCUMENTS_QUERY_KEY = "documents" as const;

export interface UseDocumentsOptions extends ListDocumentsParams {
  /** Enable / disable the query (e.g., while page is not mounted) */
  enabled?: boolean;
}

export function useDocuments(params: UseDocumentsOptions) {
  const { enabled = true, ...queryParams } = params;

  return useQuery<PaginatedDocuments, Error>({
    queryKey: [DOCUMENTS_QUERY_KEY, queryParams] as const,
    queryFn: () => listDocuments(queryParams),
    placeholderData: keepPreviousData,
    enabled,
    staleTime: 30_000, // 30s — short enough to catch concurrent edits (US-22)
    gcTime: 5 * 60_000,
  });
}
