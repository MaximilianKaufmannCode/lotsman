// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * DocumentTypesPage — admin-only management of document type catalog.
 * US-16, US-17: list, create, update. Cannot delete a type that has documents.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { Pencil, Plus, Settings2 } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import {
  useCreateDocumentType,
  useDocumentTypes,
  usePatchDocumentType,
} from "@/features/registry/hooks/useDocumentTypes";
import type { DocumentType } from "@/features/registry/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";
import { toast } from "@/shared/ui/toast";

// ── Schema ─────────────────────────────────────────────────────────────────────

const docTypeSchema = z.object({
  code: z
    .string()
    .regex(/^[a-z][a-z0-9_]{0,63}$/, "Только строчные латинские буквы, цифры и _")
    .optional(), // Optional for edit mode (code is immutable after creation)
  display_name: z.string().min(1, "Обязательное поле").max(200),
  pre_notice_days: z
    .string()
    .min(1, "Укажите хотя бы одно значение")
    .refine(
      (v) => {
        const parts = v.split(",").map((p: string) => Number(p.trim()));
        return parts.every((n: number) => Number.isInteger(n) && n > 0);
      },
      { message: "Укажите положительные целые числа через запятую" },
    ),
  notify_in_day: z.boolean().optional(),
  overdue_every_days: z.coerce.number().int().min(1, "Минимум 1").optional(),
});

type DocTypeFormValues = z.infer<typeof docTypeSchema>;

// ── Page ──────────────────────────────────────────────────────────────────────

export function DocumentTypesPage() {
  const { data: types, isLoading, isError } = useDocumentTypes();
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [editType, setEditType] = React.useState<DocumentType | null>(null);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b flex items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">Типы документов</h1>
        <Button
          size="sm"
          onClick={() => {
            setEditType(null);
            setDialogOpen(true);
          }}
        >
          <Plus className="size-4" aria-hidden />
          Добавить тип
        </Button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-auto">
        {isLoading && (
          <div className="p-6 space-y-3">
            {Array.from({ length: 4 }, (_, i) => i).map((i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        )}

        {isError && (
          <div className="p-6 text-sm text-muted-foreground">
            Не удалось загрузить типы документов
          </div>
        )}

        {!isLoading && !isError && (
          <ul aria-label="Список типов документов" className="divide-y">
            {(!types || types.length === 0) && (
              <li className="py-16 text-center text-sm text-muted-foreground">
                Нет типов документов
              </li>
            )}
            {types?.map((dt) => (
              <li key={dt.code} className="flex items-start justify-between gap-4 px-6 py-4">
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{dt.display_name}</p>
                  <p className="text-xs text-muted-foreground font-mono">{dt.code}</p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {dt.pre_notice_days.map((d) => (
                      <span
                        key={d}
                        className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs"
                      >
                        за {d} дн.
                      </span>
                    ))}
                    {dt.notify_in_day && (
                      <span className="inline-flex items-center rounded-full bg-status-soon/10 text-status-soon px-2 py-0.5 text-xs">
                        в день истечения
                      </span>
                    )}
                    <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs">
                      просрочено: каждые {dt.overdue_every_days} дн.
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <a
                    href={`/admin/document-types/${dt.code}/fields`}
                    aria-label={`Кастомные поля типа ${dt.display_name}`}
                    className="shrink-0 rounded p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    title="Кастомные поля"
                  >
                    <Settings2 className="size-4" aria-hidden />
                  </a>
                  <button
                    type="button"
                    onClick={() => {
                      setEditType(dt);
                      setDialogOpen(true);
                    }}
                    aria-label={`Редактировать тип ${dt.display_name}`}
                    className="shrink-0 rounded p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Pencil className="size-4" aria-hidden />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <DocumentTypeDialog
        open={dialogOpen}
        editType={editType}
        onClose={() => {
          setDialogOpen(false);
          setEditType(null);
        }}
      />
    </div>
  );
}

// ── Dialog ─────────────────────────────────────────────────────────────────────

function DocumentTypeDialog({
  open,
  editType,
  onClose,
}: {
  open: boolean;
  editType: DocumentType | null;
  onClose: () => void;
}) {
  const isEdit = !!editType;
  const createMutation = useCreateDocumentType();
  const patchMutation = usePatchDocumentType();
  const dialogRef = React.useRef<HTMLDivElement>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isValid },
  } = useForm<DocTypeFormValues>({
    resolver: zodResolver(docTypeSchema),
    mode: "onChange",
    defaultValues: {
      code: editType?.code ?? "",
      display_name: editType?.display_name ?? "",
      pre_notice_days: (editType?.pre_notice_days ?? [30, 7]).join(", "),
      notify_in_day: editType?.notify_in_day ?? false,
      overdue_every_days: editType?.overdue_every_days ?? 7,
    },
  });

  React.useEffect(() => {
    if (open) {
      reset({
        code: editType?.code ?? "",
        display_name: editType?.display_name ?? "",
        pre_notice_days: (editType?.pre_notice_days ?? [30, 7]).join(", "),
        notify_in_day: editType?.notify_in_day ?? false,
        overdue_every_days: editType?.overdue_every_days ?? 7,
      });
      requestAnimationFrame(() => dialogRef.current?.focus());
    }
  }, [open, editType, reset]);

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
    const preNoticeDays = values.pre_notice_days
      .split(",")
      .map((p: string) => Number(p.trim()))
      .filter((n: number) => n > 0);

    try {
      if (isEdit && editType) {
        await patchMutation.mutateAsync({
          code: editType.code,
          payload: {
            display_name: values.display_name,
            pre_notice_days: preNoticeDays,
            ...(values.notify_in_day !== undefined ? { notify_in_day: values.notify_in_day } : {}),
            ...(values.overdue_every_days !== undefined
              ? { overdue_every_days: values.overdue_every_days }
              : {}),
          },
        });
      } else {
        if (!values.code) {
          toast.show({ title: "Укажите код типа", variant: "destructive" });
          return;
        }
        await createMutation.mutateAsync({
          code: values.code,
          display_name: values.display_name,
          pre_notice_days: preNoticeDays,
          notify_in_day: values.notify_in_day ?? false,
          overdue_every_days: values.overdue_every_days ?? 7,
        });
      }
      reset();
      onClose();
    } catch {
      // Error already toasted by the mutation hooks
    }
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
        aria-labelledby="dt-dialog-title"
        tabIndex={-1}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border bg-card shadow-xl focus:outline-none p-6",
        )}
      >
        <h2 id="dt-dialog-title" className="text-base font-semibold mb-4">
          {isEdit ? "Редактировать тип документа" : "Добавить тип документа"}
        </h2>

        <form onSubmit={onSubmit} aria-label="Форма типа документа" className="space-y-4">
          {!isEdit && (
            <div>
              <label htmlFor="dt-code" className="mb-1 block text-sm font-medium">
                Код *
              </label>
              <Input
                id="dt-code"
                {...register("code")}
                placeholder="contract"
                aria-describedby={errors.code ? "dt-code-err" : undefined}
                aria-invalid={!!errors.code}
              />
              {errors.code && (
                <p id="dt-code-err" role="alert" className="mt-1 text-xs text-destructive">
                  {errors.code.message}
                </p>
              )}
            </div>
          )}

          <div>
            <label htmlFor="dt-name" className="mb-1 block text-sm font-medium">
              Отображаемое название *
            </label>
            <Input
              id="dt-name"
              {...register("display_name")}
              placeholder="Договор"
              aria-describedby={errors.display_name ? "dt-name-err" : undefined}
              aria-invalid={!!errors.display_name}
            />
            {errors.display_name && (
              <p id="dt-name-err" role="alert" className="mt-1 text-xs text-destructive">
                {errors.display_name.message}
              </p>
            )}
          </div>

          <div>
            <label htmlFor="dt-prenotice" className="mb-1 block text-sm font-medium">
              Предупреждения (дней до истечения)
            </label>
            <Input
              id="dt-prenotice"
              {...register("pre_notice_days")}
              placeholder="30, 7, 1"
              aria-describedby={errors.pre_notice_days ? "dt-prenotice-err" : "dt-prenotice-hint"}
              aria-invalid={!!errors.pre_notice_days}
            />
            <p id="dt-prenotice-hint" className="mt-1 text-xs text-muted-foreground">
              Введите числа через запятую: 60, 30, 7, 1
            </p>
            {errors.pre_notice_days && (
              <p id="dt-prenotice-err" role="alert" className="mt-1 text-xs text-destructive">
                {errors.pre_notice_days.message}
              </p>
            )}
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              {...register("notify_in_day")}
              className="rounded focus-visible:ring-2 focus-visible:ring-ring"
            />
            Уведомить в день истечения
          </label>

          <div>
            <label htmlFor="dt-overdue" className="mb-1 block text-sm font-medium">
              Повтор уведомления при просрочке (дней)
            </label>
            <Input
              id="dt-overdue"
              type="number"
              min={1}
              {...register("overdue_every_days")}
              className="w-24"
              aria-describedby={errors.overdue_every_days ? "dt-overdue-err" : undefined}
              aria-invalid={!!errors.overdue_every_days}
            />
            {errors.overdue_every_days && (
              <p id="dt-overdue-err" role="alert" className="mt-1 text-xs text-destructive">
                {errors.overdue_every_days.message}
              </p>
            )}
          </div>

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
