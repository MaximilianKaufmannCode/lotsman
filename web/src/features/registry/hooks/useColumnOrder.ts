// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tenant-wide column order hook (US-N).
 *
 * Loaded once per session (5 min stale-time — UI changes are rare).
 * Admin-only mutation invalidates the cache so other open tabs pick up
 * the change on next focus / refetch.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ColumnOrderApiError,
  type ColumnOrderResponse,
  getColumnOrder,
  updateColumnOrder,
} from "@/features/registry/columnOrderApi";
import { toast } from "@/shared/ui/toast";

export const COLUMN_ORDER_QUERY_KEY = "registry-column-order" as const;

export function useColumnOrder() {
  return useQuery<ColumnOrderResponse, Error>({
    queryKey: [COLUMN_ORDER_QUERY_KEY] as const,
    queryFn: getColumnOrder,
    staleTime: 5 * 60_000,
    gcTime: 30 * 60_000,
  });
}

export interface UpdateColumnOrderArgs {
  order: string[];
  pinned_column_id?: string | null;
}

export function useUpdateColumnOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: UpdateColumnOrderArgs) =>
      updateColumnOrder(args.order, args.pinned_column_id ?? null),
    onSuccess: (data, variables) => {
      const previous = queryClient.getQueryData<ColumnOrderResponse>([COLUMN_ORDER_QUERY_KEY]);
      queryClient.setQueryData<ColumnOrderResponse>([COLUMN_ORDER_QUERY_KEY], data);
      // Visible feedback only for pin changes — drag/arrow reorder animates
      // visibly on its own.
      if (variables.pinned_column_id && previous?.pinned_column_id !== variables.pinned_column_id) {
        toast.show({ title: "Закреплённая колонка обновлена", variant: "success" });
      }
    },
    onError: (err) => {
      const msg =
        err instanceof ColumnOrderApiError && err.status === 403
          ? "Только администратор может менять порядок колонок"
          : "Не удалось сохранить порядок колонок";
      toast.show({ title: msg, variant: "destructive" });
    },
  });
}
