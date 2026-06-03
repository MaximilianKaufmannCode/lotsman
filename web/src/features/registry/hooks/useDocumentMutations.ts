// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useDocumentMutations — create / patch / archive / restore / bulk-archive.
 *
 * Optimistic update pattern:
 *  1. Snapshot the current query cache.
 *  2. Apply the optimistic change immediately.
 *  3. On error: rollback to snapshot + show destructive toast.
 *  4. On success: show success toast; invalidate to get confirmed server state.
 *
 * Bulk archive is capped at 100 by the UI (US-23 / Q3); the API enforces it too.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "@/shared/ui/toast";
import {
  ApiError,
  archiveDocument,
  bulkArchiveDocuments,
  createDocument,
  patchDocument,
  restoreDocument,
} from "../api";
import type {
  CreateDocumentPayload,
  Document,
  PaginatedDocuments,
  PatchDocumentPayload,
} from "../types";
import { DOCUMENTS_QUERY_KEY } from "./useDocuments";
import { HISTORY_QUERY_KEY } from "./useHistory";

// ── Helpers ───────────────────────────────────────────────────────────────────

function toastError(err: unknown): void {
  const message = err instanceof ApiError ? err.message : "Неизвестная ошибка";
  toast.show({ title: "Ошибка", description: message, variant: "destructive" });
}

// ── Create ─────────────────────────────────────────────────────────────────────

export function useCreateDocument() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CreateDocumentPayload) => createDocument(payload),
    onSuccess: () => {
      toast.show({ title: "Документ добавлен", variant: "success" });
      void queryClient.invalidateQueries({ queryKey: [DOCUMENTS_QUERY_KEY] });
    },
    onError: toastError,
  });
}

// ── Patch (inline edit + form edit) ───────────────────────────────────────────

export function usePatchDocument() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: PatchDocumentPayload }) =>
      patchDocument(id, payload),

    onMutate: async ({ id, payload }) => {
      // Cancel in-flight refetches that could overwrite our optimistic update
      await queryClient.cancelQueries({ queryKey: [DOCUMENTS_QUERY_KEY] });

      // Snapshot all matching query caches
      const snapshots = queryClient.getQueriesData<PaginatedDocuments>({
        queryKey: [DOCUMENTS_QUERY_KEY],
      });

      // Optimistically update every cached page that contains this document
      queryClient.setQueriesData<PaginatedDocuments>({ queryKey: [DOCUMENTS_QUERY_KEY] }, (old) => {
        if (!old) return old;
        return {
          ...old,
          items: old.items.map((doc) => (doc.id === id ? { ...doc, ...payload } : doc)),
        };
      });

      return { snapshots };
    },

    onError: (err, _vars, context) => {
      // Rollback
      if (context?.snapshots) {
        for (const [queryKey, snapshot] of context.snapshots) {
          queryClient.setQueryData(queryKey, snapshot);
        }
      }
      toastError(err);
    },

    onSuccess: (_data, variables) => {
      toast.show({ title: "Изменения сохранены", variant: "success", duration: 3000 });
      void queryClient.invalidateQueries({ queryKey: [DOCUMENTS_QUERY_KEY] });
      // v1.25.1 — invalidate history for this doc so История изменений
      // refetches on next mount. The audit consumer pipeline takes 5-60s, so
      // immediate refetch may still return empty; the manual «Обновить»
      // button on HistoryTab covers the lag window.
      void queryClient.invalidateQueries({
        queryKey: [HISTORY_QUERY_KEY, "document", variables.id],
      });
    },
  });
}

// ── Archive (single) ──────────────────────────────────────────────────────────

export function useArchiveDocument() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => archiveDocument(id),

    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: [DOCUMENTS_QUERY_KEY] });
      const snapshots = queryClient.getQueriesData<PaginatedDocuments>({
        queryKey: [DOCUMENTS_QUERY_KEY],
      });
      // Optimistically remove from active list
      queryClient.setQueriesData<PaginatedDocuments>({ queryKey: [DOCUMENTS_QUERY_KEY] }, (old) => {
        if (!old) return old;
        return {
          ...old,
          items: old.items.filter((doc) => doc.id !== id),
          total: old.total - 1,
        };
      });
      return { snapshots };
    },

    onError: (err, _id, context) => {
      if (context?.snapshots) {
        for (const [queryKey, snapshot] of context.snapshots) {
          queryClient.setQueryData(queryKey, snapshot);
        }
      }
      toastError(err);
    },

    onSuccess: () => {
      // v1.25.3 — toast with action button «Показать архив» that flips the
      // URL filter to show archived docs. The default «active» filter would
      // otherwise hide the just-archived doc with no obvious recovery path
      // for the user (filter is buried in Filter Sheet → Метаданные →
      // Статус документа). Clicking the button sets ?doc_status=archived
      // via plain window.history.pushState (avoids dependency on TanStack
      // Router context inside a non-component hook callback).
      toast.show({
        title: "Документ архивирован",
        description: "По умолчанию архив скрыт. Откройте фильтр или нажмите ниже.",
        variant: "default",
        duration: 8000,
        action: {
          label: "Показать архив",
          onClick: () => {
            const url = new URL(window.location.href);
            url.searchParams.delete("doc_status");
            url.searchParams.append("doc_status", "archived");
            url.searchParams.delete("page");
            window.history.pushState({}, "", url.toString());
            window.dispatchEvent(new PopStateEvent("popstate"));
          },
        },
      });
      void queryClient.invalidateQueries({ queryKey: [DOCUMENTS_QUERY_KEY] });
    },
  });
}

// ── Restore (admin only) ──────────────────────────────────────────────────────

export function useRestoreDocument() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => restoreDocument(id),
    onSuccess: (restoredDoc: Document) => {
      toast.show({
        title: `Документ ${restoredDoc.number} восстановлен`,
        variant: "success",
      });
      void queryClient.invalidateQueries({ queryKey: [DOCUMENTS_QUERY_KEY] });
    },
    onError: toastError,
  });
}

// ── Bulk archive ───────────────────────────────────────────────────────────────

export const BULK_ARCHIVE_MAX = 100;

export function useBulkArchiveDocuments() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (ids: string[]) => {
      if (ids.length > BULK_ARCHIVE_MAX) {
        return Promise.reject(
          new ApiError(`Максимум ${BULK_ARCHIVE_MAX} документов за одну операцию`, 400),
        );
      }
      return bulkArchiveDocuments(ids);
    },

    onMutate: async (ids) => {
      await queryClient.cancelQueries({ queryKey: [DOCUMENTS_QUERY_KEY] });
      const snapshots = queryClient.getQueriesData<PaginatedDocuments>({
        queryKey: [DOCUMENTS_QUERY_KEY],
      });
      const idSet = new Set(ids);
      queryClient.setQueriesData<PaginatedDocuments>({ queryKey: [DOCUMENTS_QUERY_KEY] }, (old) => {
        if (!old) return old;
        return {
          ...old,
          items: old.items.filter((doc) => !idSet.has(doc.id)),
          total: old.total - ids.length,
        };
      });
      return { snapshots };
    },

    onError: (err, _ids, context) => {
      if (context?.snapshots) {
        for (const [queryKey, snapshot] of context.snapshots) {
          queryClient.setQueryData(queryKey, snapshot);
        }
      }
      toastError(err);
    },

    onSuccess: (result) => {
      toast.show({
        title: `Архивировано: ${result.archived}. Пропущено: ${result.skipped}.`,
        variant: "default",
      });
      void queryClient.invalidateQueries({ queryKey: [DOCUMENTS_QUERY_KEY] });
    },
  });
}
