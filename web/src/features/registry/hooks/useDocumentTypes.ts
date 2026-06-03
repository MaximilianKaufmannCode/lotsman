// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useDocumentTypes — TanStack Query hook for the document type catalog.
 *
 * The catalog changes rarely (managed by admins). Cached aggressively.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "@/shared/ui/toast";
import { createDocumentType, listDocumentTypes, patchDocumentType } from "../api";
import type { CreateDocumentTypePayload, DocumentType, PatchDocumentTypePayload } from "../types";

export const DOCUMENT_TYPES_QUERY_KEY = "document-types" as const;

export function useDocumentTypes() {
  return useQuery<DocumentType[], Error>({
    queryKey: [DOCUMENT_TYPES_QUERY_KEY] as const,
    queryFn: listDocumentTypes,
    staleTime: 10 * 60_000, // 10 min — catalog is slow-moving
    gcTime: 30 * 60_000,
  });
}

export function useCreateDocumentType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateDocumentTypePayload) => createDocumentType(payload),
    onSuccess: (docType: DocumentType) => {
      toast.show({
        title: `Тип документа "${docType.display_name}" добавлен`,
        variant: "success",
      });
      void queryClient.invalidateQueries({ queryKey: [DOCUMENT_TYPES_QUERY_KEY] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Ошибка при создании типа документа";
      toast.show({ title: "Ошибка", description: msg, variant: "destructive" });
    },
  });
}

export function usePatchDocumentType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ code, payload }: { code: string; payload: PatchDocumentTypePayload }) =>
      patchDocumentType(code, payload),
    onSuccess: () => {
      toast.show({ title: "Тип документа обновлён", variant: "success" });
      void queryClient.invalidateQueries({ queryKey: [DOCUMENT_TYPES_QUERY_KEY] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Ошибка при обновлении типа документа";
      toast.show({ title: "Ошибка", description: msg, variant: "destructive" });
    },
  });
}
