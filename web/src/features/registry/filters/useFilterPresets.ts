// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useFilterPresets — TanStack Query hook for saved filter presets.
 *
 * Data flow:
 * - listMySavedFilters()        → GET  /api/v1/auth/me/saved-filters
 * - createSavedFilter(body)     → POST
 * - updateSavedFilter(id, body) → PATCH /{id}
 * - deleteSavedFilter(id)       → DELETE /{id}
 *
 * Optimistic update strategy:
 * - create/update: optimistic insert + rollback on error
 * - delete:        optimistic removal + rollback on error
 *
 * Max presets: 20 — checked client-side before attempting POST.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type CreateSavedFilterPayload,
  createSavedFilter,
  deleteSavedFilter,
  listMySavedFilters,
  type SavedFilter,
  type UpdateSavedFilterPayload,
  updateSavedFilter,
} from "@/features/auth/api";
import { toast } from "@/shared/ui/toast";

export const SAVED_FILTERS_QUERY_KEY = ["saved-filters"] as const;

export const MAX_PRESETS = 20;

// ── Queries ───────────────────────────────────────────────────────────────────

export function useSavedFilters() {
  return useQuery<SavedFilter[], Error>({
    queryKey: SAVED_FILTERS_QUERY_KEY,
    queryFn: listMySavedFilters,
    staleTime: 5 * 60_000, // 5 min — presets don't change often
    gcTime: 10 * 60_000,
  });
}

// ── Mutations ─────────────────────────────────────────────────────────────────

export function useCreateSavedFilter() {
  const qc = useQueryClient();

  return useMutation<SavedFilter, Error, CreateSavedFilterPayload>({
    mutationFn: createSavedFilter,
    onMutate: async (payload) => {
      await qc.cancelQueries({ queryKey: SAVED_FILTERS_QUERY_KEY });
      const prev = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);

      // Optimistic: add a temporary item (no real id yet)
      const optimistic: SavedFilter = {
        id: `optimistic-${Date.now()}`,
        user_id: "",
        name: payload.name,
        filter_json: payload.filter_json,
        is_default: payload.is_default ?? false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, (old) => [
        ...(old ?? []),
        optimistic,
      ]);
      return { prev };
    },
    onError: (_err, _vars, context) => {
      const ctx = context as { prev?: SavedFilter[] } | undefined;
      if (ctx?.prev !== undefined) {
        qc.setQueryData(SAVED_FILTERS_QUERY_KEY, ctx.prev);
      }
      toast.show({ title: "Не удалось сохранить пресет", variant: "destructive" });
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: SAVED_FILTERS_QUERY_KEY });
      toast.show({ title: `Пресет «${saved.name}» сохранён`, variant: "success" });
    },
  });
}

export function useUpdateSavedFilter() {
  const qc = useQueryClient();

  return useMutation<SavedFilter, Error, { id: string; body: UpdateSavedFilterPayload }>({
    mutationFn: ({ id, body }) => updateSavedFilter(id, body),
    onMutate: async ({ id, body }) => {
      await qc.cancelQueries({ queryKey: SAVED_FILTERS_QUERY_KEY });
      const prev = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);

      qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, (old) =>
        (old ?? []).map((f) =>
          f.id === id
            ? {
                ...f,
                name: body.name ?? f.name,
                filter_json: body.filter_json ?? f.filter_json,
                is_default: body.is_default ?? f.is_default,
                updated_at: new Date().toISOString(),
              }
            : // If setting this as default, clear others
              body.is_default === true
              ? { ...f, is_default: false }
              : f,
        ),
      );
      return { prev };
    },
    onError: (_err, _vars, context) => {
      const ctx = context as { prev?: SavedFilter[] } | undefined;
      if (ctx?.prev !== undefined) {
        qc.setQueryData(SAVED_FILTERS_QUERY_KEY, ctx.prev);
      }
      toast.show({ title: "Не удалось обновить пресет", variant: "destructive" });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SAVED_FILTERS_QUERY_KEY });
    },
  });
}

export function useDeleteSavedFilter() {
  const qc = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: deleteSavedFilter,
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: SAVED_FILTERS_QUERY_KEY });
      const prev = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
      qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, (old) =>
        (old ?? []).filter((f) => f.id !== id),
      );
      return { prev };
    },
    onError: (_err, _vars, context) => {
      const ctx = context as { prev?: SavedFilter[] } | undefined;
      if (ctx?.prev !== undefined) {
        qc.setQueryData(SAVED_FILTERS_QUERY_KEY, ctx.prev);
      }
      toast.show({ title: "Не удалось удалить пресет", variant: "destructive" });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SAVED_FILTERS_QUERY_KEY });
      toast.show({ title: "Пресет удалён" });
    },
  });
}
