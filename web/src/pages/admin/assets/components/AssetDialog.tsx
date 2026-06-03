// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * AssetDialog — create or edit an asset (partner company).
 * INN validation: 10 digits (legal entity) or 12 digits (individual) + ФНС checksum (Q6).
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { X } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import type { Asset } from "@/features/registry/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";

// ── INN checksum validation (ФНС algorithm) ───────────────────────────────────

function validateInn(inn: string): boolean {
  if (!/^\d+$/.test(inn)) return false;
  if (inn.length === 10) {
    const weights = [2, 4, 10, 3, 5, 9, 4, 6, 8];
    const sum = weights.reduce((acc, w, i) => acc + w * Number(inn[i]), 0);
    const checkDigit = (sum % 11) % 10;
    return checkDigit === Number(inn[9]);
  }
  if (inn.length === 12) {
    const w1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8];
    const w2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8];
    const d10 = (w1.reduce((acc, w, i) => acc + w * Number(inn[i]), 0) % 11) % 10;
    const d11 = (w2.reduce((acc, w, i) => acc + w * Number(inn[i]), 0) % 11) % 10;
    return d10 === Number(inn[10]) && d11 === Number(inn[11]);
  }
  return false;
}

// ── Schema ─────────────────────────────────────────────────────────────────────

const assetSchema = z.object({
  name: z.string().min(1, "Обязательное поле").max(500),
  inn: z
    .string()
    .nullable()
    .optional()
    .refine((val) => !val || validateInn(val), {
      message: "Некорректный ИНН (10 или 12 цифр, проверочная цифра)",
    }),
  notes: z.string().max(5000).nullable().optional(),
});

type AssetFormValues = z.infer<typeof assetSchema>;

// ── Dialog ─────────────────────────────────────────────────────────────────────

interface AssetDialogProps {
  open: boolean;
  asset?: Asset | null;
  onClose: () => void;
  onSubmit: (values: { name: string; inn: string | null; notes: string | null }) => Promise<void>;
}

export function AssetDialog({ open, asset, onClose, onSubmit }: AssetDialogProps) {
  const isEdit = !!asset;
  const dialogRef = React.useRef<HTMLDivElement>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isValid },
  } = useForm<AssetFormValues>({
    resolver: zodResolver(assetSchema),
    mode: "onChange",
    defaultValues: {
      name: asset?.name ?? "",
      inn: asset?.inn ?? "",
      notes: asset?.notes ?? "",
    },
  });

  React.useEffect(() => {
    if (open) {
      reset({
        name: asset?.name ?? "",
        inn: asset?.inn ?? "",
        notes: asset?.notes ?? "",
      });
      requestAnimationFrame(() => dialogRef.current?.focus());
    }
  }, [open, asset, reset]);

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

  const handleFormSubmit = handleSubmit(async (values) => {
    await onSubmit({
      name: values.name,
      inn: values.inn ?? null,
      notes: values.notes ?? null,
    });
    reset();
    onClose();
  });

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        aria-hidden="true"
        onClick={() => {
          reset();
          onClose();
        }}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="asset-dialog-title"
        tabIndex={-1}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border bg-card shadow-xl focus:outline-none",
        )}
      >
        <div className="flex items-center justify-between p-4 border-b">
          <h2 id="asset-dialog-title" className="text-base font-semibold">
            {isEdit ? "Редактировать контрагента" : "Добавить контрагента"}
          </h2>
          <button
            type="button"
            onClick={() => {
              reset();
              onClose();
            }}
            aria-label="Закрыть"
            className="rounded p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>

        <form
          onSubmit={handleFormSubmit}
          aria-label={isEdit ? "Форма редактирования контрагента" : "Форма создания контрагента"}
          className="p-4 space-y-4"
        >
          <FormField label="Название *" error={errors.name?.message}>
            <Input
              {...register("name")}
              placeholder="ООО «Название компании»"
              aria-required="true"
            />
          </FormField>

          <FormField
            label="ИНН"
            error={errors.inn?.message}
            hint="10 цифр (юрлицо) или 12 цифр (ИП)"
          >
            <Input
              {...register("inn")}
              placeholder="7701234567"
              inputMode="numeric"
              maxLength={12}
            />
          </FormField>

          <FormField label="Заметки" error={errors.notes?.message}>
            <textarea
              {...register("notes")}
              rows={3}
              className={cn(
                "w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring placeholder:text-muted-foreground",
              )}
              placeholder="Дополнительные сведения..."
            />
          </FormField>

          <div className="flex gap-2 pt-1">
            <Button type="submit" disabled={isSubmitting || !isValid}>
              {isSubmitting ? "Сохранение..." : isEdit ? "Сохранить" : "Создать"}
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

function FormField({
  label,
  error,
  hint,
  children,
}: {
  label: string;
  error?: string | undefined;
  hint?: string | undefined;
  children: React.ReactNode;
}) {
  const id = React.useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errId].filter((x): x is string => x !== undefined).join(" ");

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
              ...(describedBy ? { "aria-describedby": describedBy } : {}),
              "aria-invalid": !!error,
            },
          )
        : children}
      {hint && (
        <p id={hintId} className="mt-1 text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errId} role="alert" className="mt-1 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
