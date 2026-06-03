// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useAttachments — TanStack Query + XHR upload for document attachments.
 *
 * Upload uses XMLHttpRequest (not fetch) because TanStack Query's mutationFn
 * doesn't expose upload progress events. The upload is wrapped in a Promise
 * and wired to React state for progress tracking.
 *
 * Allowed MIME types (Q7): PDF, JPEG, PNG, TIFF, DOCX, XLSX.
 * Max size (Q1): 25 MiB. Both are also enforced server-side.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "@/shared/ui/toast";
import {
  ApiError,
  deleteAttachment,
  listAttachments,
  type UploadProgress,
  uploadAttachment,
} from "../api";
import type { Attachment } from "../types";

export const ATTACHMENTS_QUERY_KEY = "attachments" as const;

/** 25 MiB in bytes */
export const MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024;

export const ALLOWED_MIME_TYPES = [
  "application/pdf",
  "image/jpeg",
  "image/png",
  "image/tiff",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
] as const;

// ── Query ─────────────────────────────────────────────────────────────────────

export function useAttachments(documentId: string | null) {
  return useQuery<Attachment[], Error>({
    queryKey: [ATTACHMENTS_QUERY_KEY, documentId] as const,
    queryFn: () => {
      if (!documentId) return Promise.resolve([]);
      return listAttachments(documentId);
    },
    enabled: !!documentId,
    staleTime: 60_000,
  });
}

// ── Upload with progress ──────────────────────────────────────────────────────

export interface UploadState {
  file: File;
  progress: UploadProgress | null;
  error: string | null;
  done: boolean;
}

export interface UseUploadAttachmentReturn {
  uploads: UploadState[];
  upload: (documentId: string, file: File) => void;
  clearDone: () => void;
}

export function useUploadAttachment(): UseUploadAttachmentReturn {
  const queryClient = useQueryClient();
  const [uploads, setUploads] = React.useState<UploadState[]>([]);

  const upload = React.useCallback(
    (documentId: string, file: File) => {
      // Client-side pre-validation (server also validates)
      if (file.size > MAX_ATTACHMENT_SIZE_BYTES) {
        toast.show({
          title: "Файл слишком большой",
          description: "Файл больше 25 МиБ",
          variant: "destructive",
        });
        return;
      }

      const entry: UploadState = { file, progress: null, error: null, done: false };
      setUploads((prev) => [...prev, entry]);
      const idx = uploads.length; // stable index for this upload

      const updateEntry = (patch: Partial<UploadState>) => {
        setUploads((prev) => prev.map((u, i) => (i === idx ? { ...u, ...patch } : u)));
      };

      uploadAttachment(documentId, file, (progress) => {
        updateEntry({ progress });
      })
        .then(() => {
          updateEntry({ done: true, progress: { loaded: file.size, total: file.size } });
          toast.show({ title: `Файл "${file.name}" загружен`, variant: "success" });
          void queryClient.invalidateQueries({
            queryKey: [ATTACHMENTS_QUERY_KEY, documentId],
          });
        })
        .catch((err: unknown) => {
          const status = err instanceof ApiError ? err.status : 0;
          let errorMsg: string;
          if (status === 413) {
            errorMsg = "Файл больше 25 МиБ";
          } else if (status === 415) {
            errorMsg = "Тип файла не поддерживается";
          } else if (status === 409) {
            errorMsg = "Нельзя добавить вложение к архивному документу";
          } else {
            errorMsg = err instanceof Error ? err.message : "Ошибка загрузки";
          }
          updateEntry({ error: errorMsg });
          toast.show({ title: "Ошибка загрузки", description: errorMsg, variant: "destructive" });
        });
    },
    [uploads.length, queryClient],
  );

  const clearDone = React.useCallback(() => {
    setUploads((prev) => prev.filter((u) => !u.done && !u.error));
  }, []);

  return { uploads, upload, clearDone };
}

// ── Delete ─────────────────────────────────────────────────────────────────────

export function useDeleteAttachment(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (attachmentId: string) => deleteAttachment(attachmentId),
    onSuccess: () => {
      toast.show({ title: "Вложение удалено", variant: "default" });
      void queryClient.invalidateQueries({
        queryKey: [ATTACHMENTS_QUERY_KEY, documentId],
      });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Ошибка при удалении вложения";
      toast.show({ title: "Ошибка", description: msg, variant: "destructive" });
    },
  });
}
