// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useDistinctValues — TanStack Query hook for per-field distinct value autocomplete.
 *
 * Backend: GET /api/v1/documents/distinct-values?field=<field>&q=<q>&limit=<limit>
 * Cache: staleTime 5 min (matches Redis TTL on backend).
 * Loaded lazily — triggered only when a filter popover is opened for the first time.
 */

import { useQuery } from "@tanstack/react-query";
import {
  type DistinctValuesResponse,
  type ListDistinctValuesParams,
  listDistinctValues,
} from "../api";

export const DISTINCT_VALUES_QUERY_KEY = "distinct-values" as const;

export function useDistinctValues(
  params: ListDistinctValuesParams,
  options?: { enabled?: boolean },
) {
  return useQuery<DistinctValuesResponse, Error>({
    queryKey: [DISTINCT_VALUES_QUERY_KEY, params.field, params.q ?? "", params.limit ?? 100],
    queryFn: () => listDistinctValues(params),
    staleTime: 5 * 60_000, // 5 min — matches backend Redis TTL
    gcTime: 10 * 60_000,
    enabled: options?.enabled !== false && Boolean(params.field),
    retry: 1,
  });
}
