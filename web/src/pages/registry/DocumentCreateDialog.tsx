// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * DocumentCreateDialog — modal for creating a new document.
 *
 * Keeps the registry table in context (no page navigation).
 * Required fields driven by document_type selection.
 * Asset autocomplete with combobox.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { X } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useAuth } from "@/features/auth/AuthProvider";
import { useActiveAssets } from "@/features/registry/hooks/useAssets";
import { useCreateDocument } from "@/features/registry/hooks/useDocumentMutations";
import { useDocumentTypes } from "@/features/registry/hooks/useDocumentTypes";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";

// ── Schema ─────────────────────────────────────────────────────────────────────

const createDocSchema = z.object({
  asset_id: z.string().min(1, "Выберите контрагента"),
  type_code: z.string().min(1, "Выберите тип документа"),
  number: z.string().min(1, "Обязательное поле"),
  issue_date: z.string().nullable().optional(),
  expiry_date: z.string().nullable().optional(),
  notes: z.string().max(10000, "Максимум 10 000 символов").nullable().optional(),
});

type CreateDocFormValues = z.infer<typeof createDocSchema>;

// ── Dialog ────────────────────────────────────────────────────────────────────

interface DocumentCreateDialogProps {
  open: boolean;
  onClose: () => void;
}

export function DocumentCreateDialog({ open, onClose }: DocumentCreateDialogProps) {
  const { claims } = useAuth();
  const dialogRef = React.useRef<HTMLDivElement>(null);
  const firstInputRef = React.useRef<HTMLSelectElement>(null);
  const prevFocusRef = React.useRef<HTMLElement | null>(null);

  const { data: assetsData, isLoading: assetsLoading } = useActiveAssets();
  const { data: docTypes, isLoading: typesLoading } = useDocumentTypes();
  const createMutation = useCreateDocument();

  const {
    register,
    handleSubmit,
    watch,
    reset,
    formState: { errors, isSubmitting, isValid },
  } = useForm<CreateDocFormValues>({
    resolver: zodResolver(createDocSchema),
    mode: "onChange",
    defaultValues: {
      asset_id: "",
      type_code: "",
      number: "",
      issue_date: null,
      expiry_date: null,
      notes: null,
    },
  });

  const selectedTypeCode = watch("type_code");
  const selectedType = docTypes?.find((t) => t.code === selectedTypeCode);

  // Focus first input on open
  React.useEffect(() => {
    if (open) {
      prevFocusRef.current = document.activeElement as HTMLElement;
      requestAnimationFrame(() => firstInputRef.current?.focus());
    }
  }, [open]);

  // Restore focus on close
  React.useEffect(() => {
    if (!open && prevFocusRef.current) {
      prevFocusRef.current.focus();
      prevFocusRef.current = null;
    }
  }, [open]);

  // Close on Escape
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        reset();
        onClose();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose, reset]);

  const onSubmit = handleSubmit(async (values) => {
    await createMutation.mutateAsync({
      asset_id: values.asset_id,
      type_code: values.type_code,
      number: values.number,
      issue_date: values.issue_date ?? null,
      expiry_date: values.expiry_date ?? null,
      responsible_user_id: claims?.sub ?? null,
      notes: values.notes ?? null,
    });
    reset();
    onClose();
  });

  if (!open) return null;

  const isLoading = assetsLoading || typesLoading;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        aria-hidden="true"
        onClick={() => {
          reset();
          onClose();
        }}
      />

      {/* Dialog */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-dialog-title"
        aria-describedby="create-dialog-desc"
        tabIndex={-1}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border bg-card text-card-foreground shadow-xl focus:outline-none",
          "max-h-[90vh] flex flex-col",
        )}
      >
        <div className="flex items-center justify-between p-6 pb-2 shrink-0">
          <div>
            <h2 id="create-dialog-title" className="text-lg font-semibold">
              Добавить документ
            </h2>
            <p id="create-dialog-desc" className="mt-0.5 text-sm text-muted-foreground">
              Заполните обязательные поля для регистрации документа.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              reset();
              onClose();
            }}
            aria-label="Закрыть"
            className="rounded p-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>

        <form
          onSubmit={onSubmit}
          aria-label="Форма создания документа"
          className="flex-1 overflow-y-auto p-6 pt-4 space-y-4"
        >
          {isLoading && <p className="text-sm text-muted-foreground">Загрузка данных...</p>}

          {/* Контрагент */}
          <FormField label="Контрагент *" error={errors.asset_id?.message}>
            <select
              {...register("asset_id")}
              ref={(el) => {
                if (el) firstInputRef.current = el;
                const { ref } = register("asset_id");
                if (typeof ref === "function") ref(el);
              }}
              aria-required="true"
              className={cn(
                "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              <option value="">— Выберите контрагента —</option>
              {assetsData?.items.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </FormField>

          {/* Тип документа */}
          <FormField label="Тип документа *" error={errors.type_code?.message}>
            <select
              {...register("type_code")}
              aria-required="true"
              className={cn(
                "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              <option value="">
                {(!docTypes || docTypes.length === 0) && !typesLoading
                  ? "Нет доступных типов"
                  : "— Выберите тип —"}
              </option>
              {docTypes?.map((dt) => (
                <option key={dt.code} value={dt.code}>
                  {dt.display_name}
                </option>
              ))}
            </select>
          </FormField>

          {/* Pre-notice info */}
          {selectedType && (
            <div className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
              Уведомления: за {selectedType.pre_notice_days.join(", ")} дней
              {selectedType.notify_in_day ? " + в день истечения" : ""}
              {" · "}просрочено: каждые {selectedType.overdue_every_days} дней
            </div>
          )}

          {/* № документа */}
          <FormField label="№ документа *" error={errors.number?.message}>
            <Input
              {...register("number")}
              placeholder="Например: ДГ-2026-001"
              aria-required="true"
            />
          </FormField>

          {/* Dates */}
          <div className="grid grid-cols-2 gap-3">
            <FormField label="Дата выдачи">
              <Input type="date" {...register("issue_date")} />
            </FormField>
            <FormField label="Действует до">
              <Input type="date" {...register("expiry_date")} />
            </FormField>
          </div>

          {/* Notes */}
          <FormField label="Заметки" error={errors.notes?.message}>
            <textarea
              {...register("notes")}
              rows={3}
              placeholder="Дополнительные сведения..."
              className={cn(
                "w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                "placeholder:text-muted-foreground",
              )}
            />
          </FormField>

          {/* Admin-only asset create hint */}
          {claims?.role === "admin" && (
            <p className="text-xs text-muted-foreground">
              Контрагент не найден?{" "}
              <a
                href="/admin/assets"
                className="text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                Добавить контрагента
              </a>
            </p>
          )}

          {/* Actions */}
          <div className="flex gap-2 pt-2">
            <Button type="submit" disabled={isSubmitting || createMutation.isPending || !isValid}>
              {isSubmitting || createMutation.isPending ? "Сохранение..." : "Создать документ"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                reset();
                onClose();
              }}
            >
              Отмена
            </Button>
          </div>
        </form>
      </div>
    </>
  );
}

// ── FormField helper ──────────────────────────────────────────────────────────

function FormField({
  label,
  error,
  children,
}: {
  label: string;
  error?: string | undefined;
  children: React.ReactNode;
}) {
  const id = React.useId();
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium">
        {label}
      </label>
      {React.isValidElement(children)
        ? React.cloneElement(
            children as React.ReactElement<{
              id?: string | undefined;
              "aria-describedby"?: string | undefined;
              "aria-invalid"?: boolean | undefined;
            }>,
            {
              id,
              ...(error ? { "aria-describedby": `${id}-error` } : {}),
              "aria-invalid": !!error,
            },
          )
        : children}
      {error && (
        <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
