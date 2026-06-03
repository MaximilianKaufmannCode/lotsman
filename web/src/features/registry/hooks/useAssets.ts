// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useAssets — TanStack Query hook for the asset (partner company) list.
 *
 * Cached aggressively (staleTime: 5 min) because assets change rarely and
 * the list is used in dropdowns across multiple components.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "@/shared/ui/toast";
import { archiveAsset, createAsset, type ListAssetsParams, listAssets, patchAsset } from "../api";
import type { Asset, CreateAssetPayload, PaginatedAssets, PatchAssetPayload } from "../types";

export const ASSETS_QUERY_KEY = "assets" as const;

export function useAssets(params?: ListAssetsParams) {
  return useQuery<PaginatedAssets, Error>({
    queryKey: [ASSETS_QUERY_KEY, params] as const,
    queryFn: () => listAssets(params),
    staleTime: 5 * 60_000,
    gcTime: 15 * 60_000,
  });
}

/** All active assets — for combobox dropdowns in forms */
export function useActiveAssets() {
  return useQuery<PaginatedAssets, Error>({
    queryKey: [ASSETS_QUERY_KEY, { show_archived: false }] as const,
    queryFn: () => listAssets({ show_archived: false, page_size: 500 }),
    staleTime: 5 * 60_000,
    gcTime: 15 * 60_000,
  });
}

// ── Mutations ─────────────────────────────────────────────────────────────────

export function useCreateAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateAssetPayload) => createAsset(payload),
    onSuccess: (asset: Asset) => {
      toast.show({ title: `Контрагент "${asset.name}" добавлен`, variant: "success" });
      void queryClient.invalidateQueries({ queryKey: [ASSETS_QUERY_KEY] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Ошибка при создании контрагента";
      toast.show({ title: "Ошибка", description: msg, variant: "destructive" });
    },
  });
}

export function usePatchAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: PatchAssetPayload }) =>
      patchAsset(id, payload),
    onSuccess: () => {
      toast.show({ title: "Контрагент обновлён", variant: "success" });
      void queryClient.invalidateQueries({ queryKey: [ASSETS_QUERY_KEY] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Ошибка при обновлении контрагента";
      toast.show({ title: "Ошибка", description: msg, variant: "destructive" });
    },
  });
}

export function useArchiveAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => archiveAsset(id),
    onSuccess: () => {
      toast.show({ title: "Контрагент архивирован", variant: "default" });
      void queryClient.invalidateQueries({ queryKey: [ASSETS_QUERY_KEY] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Ошибка при архивировании";
      toast.show({ title: "Ошибка", description: msg, variant: "destructive" });
    },
  });
}
