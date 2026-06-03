// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * CustomFieldsPage — admin editor for per-document-type custom field schema.
 *
 * Route: /admin/document-types/:typeCode/fields
 * US-6: Define custom field schema per document type.
 * US-7: Re-MFA required for schema changes.
 *
 * Architecture:
 *  - Local schema state (not server state) — edits are staged locally until
 *    "Сохранить" is clicked.
 *  - TanStack Query for initial fetch and post-save invalidation.
 *  - TOTP inline (bottom panel) on save, same pattern as ChannelsPage.
 *  - react-hook-form + zod for the "Add field" modal form.
 *  - Drag-to-reorder via HTML5 drag API (no extra lib required).
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { GripVertical, Plus, Trash2 } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import {
  type CustomField,
  CustomFieldApiResponseError,
  type FieldType,
  getCustomFieldSchema,
  updateCustomFieldSchema,
} from "@/features/admin/document-types/custom-fields-api";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";
import { toast } from "@/shared/ui/toast";

// ── Zod schema for add-field form ─────────────────────────────────────────────

const FIELD_TYPE_OPTIONS: { value: FieldType; label: string }[] = [
  { value: "text", label: "Текст" },
  { value: "number", label: "Число" },
  { value: "date", label: "Дата" },
  { value: "enum", label: "Список (enum)" },
];

const addFieldSchema = z
  .object({
    display_name: z.string().min(1, "Обязательное поле").max(100, "Не более 100 символов"),
    key: z
      .string()
      .regex(/^[a-z][a-z0-9_]{0,63}$/, "Только строчные буквы, цифры и _, первый символ — буква")
      .min(1, "Обязательное поле"),
    type: z.enum(["text", "number", "date", "enum"]),
    required: z.boolean(),
    options: z.string().optional(), // newline- or comma-separated for enum
  })
  .superRefine((val, ctx) => {
    if (val.type === "enum") {
      const opts = parseOptions(val.options ?? "");
      if (opts.length === 0) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["options"],
          message: "Для enum-поля требуется хотя бы один вариант",
        });
      }
    }
  });

type AddFieldFormValues = z.infer<typeof addFieldSchema>;

function parseOptions(raw: string): string[] {
  return raw
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// ── Error code messages ───────────────────────────────────────────────────────

const ERROR_MSG: Record<string, string> = {
  REMFA_REQUIRED: "Для сохранения необходим TOTP-код.",
  REMFA_REPLAY: "Этот TOTP-код уже использован. Дождитесь следующего (30 с).",
  CUSTOM_FIELD_VALIDATION: "Ошибка валидации схемы. Проверьте поля.",
};

function mapError(err: unknown): string {
  if (err instanceof CustomFieldApiResponseError && err.code) {
    return ERROR_MSG[err.code] ?? "Произошла ошибка. Попробуйте снова.";
  }
  return err instanceof Error ? err.message : "Произошла ошибка. Попробуйте снова.";
}

// ── Page ──────────────────────────────────────────────────────────────────────

interface CustomFieldsPageProps {
  typeCode: string;
}

export function CustomFieldsPage({ typeCode }: CustomFieldsPageProps) {
  const queryClient = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["doc-type-fields", typeCode],
    queryFn: () => getCustomFieldSchema(typeCode),
    staleTime: 5 * 60_000,
  });

  // Local staged schema — diverges from server until saved
  const [schema, setSchema] = React.useState<CustomField[]>([]);
  const [isDirty, setIsDirty] = React.useState(false);

  // Sync from server on first load (only if not dirty)
  React.useEffect(() => {
    if (data && !isDirty) {
      setSchema(data);
    }
  }, [data, isDirty]);

  // ── Add field modal ────────────────────────────────────────────────────────
  const [addOpen, setAddOpen] = React.useState(false);

  // ── Delete confirm dialog ──────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = React.useState<CustomField | null>(null);

  // ── Save / TOTP panel ──────────────────────────────────────────────────────
  const [totpValue, setTotpValue] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const totpRef = React.useRef<HTMLInputElement>(null);

  // ── Drag state ─────────────────────────────────────────────────────────────
  const dragIdx = React.useRef<number | null>(null);

  const handleDragStart = (idx: number) => {
    dragIdx.current = idx;
  };

  const handleDragOver = (e: React.DragEvent, idx: number) => {
    e.preventDefault();
    if (dragIdx.current === null || dragIdx.current === idx) return;
    const next = [...schema];
    const spliced = next.splice(dragIdx.current, 1);
    const item = spliced[0];
    if (!item) return;
    next.splice(idx, 0, item);
    dragIdx.current = idx;
    setSchema(next);
    setIsDirty(true);
  };

  const handleDragEnd = () => {
    dragIdx.current = null;
  };

  // ── Field ops ──────────────────────────────────────────────────────────────

  function addField(values: AddFieldFormValues) {
    const opts = values.type === "enum" ? parseOptions(values.options ?? "") : null;
    const field: CustomField = {
      key: values.key,
      display_name: values.display_name,
      type: values.type,
      required: values.required,
      options: opts,
    };
    // Guard duplicate key
    if (schema.some((f) => f.key === field.key)) {
      toast.show({
        title: `Поле с ключом "${field.key}" уже существует`,
        variant: "destructive",
      });
      return;
    }
    setSchema((prev) => [...prev, field]);
    setIsDirty(true);
    setAddOpen(false);
    toast.show({ title: `Поле «${field.display_name}» добавлено`, variant: "success" });
  }

  function confirmDelete(field: CustomField) {
    setSchema((prev) => prev.filter((f) => f.key !== field.key));
    setIsDirty(true);
    setDeleteTarget(null);
    toast.show({ title: `Поле «${field.display_name}» удалено`, variant: "success" });
  }

  // ── Save handler ───────────────────────────────────────────────────────────

  async function handleSave() {
    const code = totpValue.trim();
    if (code.length !== 6 || !/^\d{6}$/.test(code)) {
      setTotpError("Введите 6-значный TOTP-код");
      totpRef.current?.focus();
      return;
    }

    setSaving(true);
    setTotpError(null);

    try {
      const saved = await updateCustomFieldSchema(typeCode, schema, code);
      setSchema(saved);
      setIsDirty(false);
      setTotpValue("");
      await queryClient.invalidateQueries({ queryKey: ["doc-type-fields", typeCode] });
      toast.show({ title: "Схема полей сохранена", variant: "success" });
    } catch (err) {
      if (err instanceof CustomFieldApiResponseError && err.code === "REMFA_REPLAY") {
        setTotpValue("");
        setTotpError(
          ERROR_MSG.REMFA_REPLAY ?? "TOTP-код уже использован. Дождитесь следующего (30 с).",
        );
        requestAnimationFrame(() => totpRef.current?.focus());
      } else {
        setTotpError(mapError(err));
      }
    } finally {
      setSaving(false);
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b flex items-center gap-4">
        <a
          href="/admin/document-types"
          className="text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        >
          ← К типам документов
        </a>
        <h1 className="text-xl font-semibold flex-1">
          Кастомные поля типа документа: <span className="font-mono text-base">{typeCode}</span>
        </h1>
        <Button size="sm" onClick={() => setAddOpen(true)} data-testid="add-custom-field-btn">
          <Plus className="size-4" aria-hidden />
          Добавить поле
        </Button>
      </div>

      {/* Schema table */}
      <div className="flex-1 overflow-auto">
        {isLoading && !isDirty && (
          <div className="p-6 space-y-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        )}

        {isError && !isDirty && (
          <div className="p-6 text-sm text-muted-foreground">Не удалось загрузить схему полей</div>
        )}

        {(!isLoading || isDirty) &&
          (schema.length === 0 ? (
            <div className="py-16 text-center text-sm text-muted-foreground">
              Кастомных полей нет. Нажмите «Добавить поле».
            </div>
          ) : (
            <table className="w-full text-sm border-collapse" aria-label="Кастомные поля схемы">
              <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur-sm border-b">
                <tr>
                  <th
                    scope="col"
                    className="px-3 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider w-8"
                  >
                    <span className="sr-only">Перетащить</span>
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider"
                  >
                    Отображаемое название
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider"
                  >
                    Ключ
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider"
                  >
                    Тип
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider"
                  >
                    Обязательное
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider"
                  >
                    Варианты (enum)
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider w-16"
                  >
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {schema.map((field, idx) => (
                  <tr
                    key={field.key}
                    draggable
                    onDragStart={() => handleDragStart(idx)}
                    onDragOver={(e) => handleDragOver(e, idx)}
                    onDragEnd={handleDragEnd}
                    className="border-b hover:bg-muted/30 cursor-grab active:cursor-grabbing"
                    data-testid={`field-row-${field.key}`}
                  >
                    <td className="px-3 py-3 text-muted-foreground">
                      <GripVertical className="size-4" aria-hidden />
                    </td>
                    <td className="px-3 py-3 font-medium">{field.display_name}</td>
                    <td className="px-3 py-3">
                      <span className="font-mono text-xs bg-muted rounded px-1.5 py-0.5">
                        {field.key}
                      </span>
                    </td>
                    <td className="px-3 py-3 capitalize">
                      {FIELD_TYPE_OPTIONS.find((o) => o.value === field.type)?.label ?? field.type}
                    </td>
                    <td className="px-3 py-3">
                      {field.required ? (
                        <span className="text-status-ok text-xs font-medium">Да</span>
                      ) : (
                        <span className="text-muted-foreground text-xs">Нет</span>
                      )}
                    </td>
                    <td className="px-3 py-3 text-xs text-muted-foreground max-w-[200px]">
                      {field.type === "enum" && field.options ? field.options.join(", ") : "—"}
                    </td>
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        onClick={() => setDeleteTarget(field)}
                        aria-label={`Удалить поле ${field.display_name}`}
                        className="rounded p-1 text-muted-foreground hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <Trash2 className="size-4" aria-hidden />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </div>

      {/* Save panel with TOTP */}
      <div className="shrink-0 border-t px-6 py-4 bg-muted/20">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="cf-totp" className="text-sm font-medium">
              Код TOTP для сохранения
            </label>
            <Input
              id="cf-totp"
              ref={totpRef}
              type="text"
              inputMode="numeric"
              maxLength={6}
              pattern="\d{6}"
              placeholder="123456"
              value={totpValue}
              onChange={(e) => {
                setTotpValue(e.target.value.replace(/\D/g, "").slice(0, 6));
                setTotpError(null);
              }}
              aria-describedby={totpError ? "cf-totp-err" : "cf-totp-hint"}
              aria-invalid={!!totpError}
              disabled={saving}
              className="w-36"
              data-testid="cf-totp-input"
            />
            <p id="cf-totp-hint" className="text-xs text-muted-foreground">
              6-значный код из вашего приложения-аутентификатора
            </p>
            {totpError && (
              <p
                id="cf-totp-err"
                role="alert"
                className="text-xs text-destructive"
                aria-live="polite"
              >
                {totpError}
              </p>
            )}
          </div>
          <Button
            onClick={() => void handleSave()}
            disabled={saving || !isDirty}
            data-testid="cf-save-btn"
          >
            {saving ? "Сохранение..." : "Сохранить схему"}
          </Button>
          {!isDirty && (
            <span className="text-xs text-muted-foreground self-center">
              Нет несохранённых изменений
            </span>
          )}
        </div>
      </div>

      {/* Add field modal */}
      {addOpen && (
        <AddFieldModal
          existingKeys={schema.map((f) => f.key)}
          onAdd={addField}
          onClose={() => setAddOpen(false)}
        />
      )}

      {/* Delete confirm dialog */}
      {deleteTarget && (
        <DeleteFieldConfirm
          field={deleteTarget}
          onConfirm={() => confirmDelete(deleteTarget)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}

// ── AddFieldModal ──────────────────────────────────────────────────────────────

function AddFieldModal({
  existingKeys,
  onAdd,
  onClose,
}: {
  existingKeys: string[];
  onAdd: (values: AddFieldFormValues) => void;
  onClose: () => void;
}) {
  const dialogRef = React.useRef<HTMLDivElement>(null);

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
    reset,
  } = useForm<AddFieldFormValues>({
    resolver: zodResolver(addFieldSchema),
    mode: "onChange",
    defaultValues: {
      display_name: "",
      key: "",
      type: "text",
      required: false,
      options: "",
    },
  });

  const selectedType = watch("type");

  // Auto-derive key from display_name (only if user hasn't typed in the key field yet)
  const displayName = watch("display_name");
  const [keyManuallySet, setKeyManuallySet] = React.useState(false);
  React.useEffect(() => {
    if (!keyManuallySet && displayName) {
      const derived = displayName
        .toLowerCase()
        .replace(/[^a-z0-9]/g, "_")
        .replace(/^([^a-z])/, "f$1")
        .replace(/_+/g, "_")
        .replace(/_+$/, "")
        .slice(0, 64);
      if (derived) {
        setValue("key", derived, { shouldValidate: true });
      }
    }
  }, [displayName, keyManuallySet, setValue]);

  // Focus the dialog on mount so screen readers announce the dialog title.
  // autoFocus on the first input handles interactive focus — this sets
  // programmatic focus on the container for SR context.
  React.useEffect(() => {
    // Only focus the dialog container if nothing inside it has focus yet
    // (autoFocus on the first input fires before this effect in React 19).
    const active = document.activeElement;
    if (!dialogRef.current?.contains(active)) {
      dialogRef.current?.focus();
    }
  }, []);

  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        reset();
        onClose();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose, reset]);

  function onSubmit(values: AddFieldFormValues) {
    if (existingKeys.includes(values.key)) {
      // already handled in parent, but guard here for UX
    }
    onAdd(values);
    reset();
  }

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
        aria-labelledby="add-field-title"
        tabIndex={-1}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border bg-card shadow-xl focus:outline-none p-6",
        )}
        data-testid="add-field-modal"
      >
        <h2 id="add-field-title" className="text-base font-semibold mb-4">
          Добавить кастомное поле
        </h2>

        <form
          onSubmit={handleSubmit(onSubmit)}
          aria-label="Форма добавления поля"
          className="space-y-4"
        >
          {/* Display name */}
          <div>
            <label htmlFor="cf-display-name" className="mb-1 block text-sm font-medium">
              Отображаемое название *
            </label>
            <Input
              id="cf-display-name"
              {...register("display_name")}
              placeholder="Дата регистрации"
              aria-describedby={errors.display_name ? "cf-dn-err" : undefined}
              aria-invalid={!!errors.display_name}
              autoFocus
            />
            {errors.display_name && (
              <p id="cf-dn-err" role="alert" className="mt-1 text-xs text-destructive">
                {errors.display_name.message}
              </p>
            )}
          </div>

          {/* Key */}
          <div>
            <label htmlFor="cf-key" className="mb-1 block text-sm font-medium">
              Ключ (идентификатор) *
            </label>
            <Input
              id="cf-key"
              {...register("key", {
                onChange: () => setKeyManuallySet(true),
              })}
              placeholder="registration_date"
              className="font-mono"
              aria-describedby={errors.key ? "cf-key-err" : "cf-key-hint"}
              aria-invalid={!!errors.key}
            />
            <p id="cf-key-hint" className="mt-1 text-xs text-muted-foreground">
              Латинские строчные, цифры, _ (начинается с буквы)
            </p>
            {errors.key && (
              <p id="cf-key-err" role="alert" className="mt-1 text-xs text-destructive">
                {errors.key.message}
              </p>
            )}
          </div>

          {/* Type */}
          <div>
            <label htmlFor="cf-type" className="mb-1 block text-sm font-medium">
              Тип поля *
            </label>
            <select
              id="cf-type"
              {...register("type")}
              className="w-full rounded border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {FIELD_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          {/* Required */}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              {...register("required")}
              className="rounded focus-visible:ring-2 focus-visible:ring-ring"
            />
            Обязательное поле
          </label>

          {/* Options (enum only) */}
          {selectedType === "enum" && (
            <div>
              <label htmlFor="cf-options" className="mb-1 block text-sm font-medium">
                Варианты (один на строку или через запятую) *
              </label>
              <textarea
                id="cf-options"
                {...register("options")}
                rows={4}
                placeholder={"Активен\nНеактивен\nПриостановлен"}
                aria-describedby={errors.options ? "cf-opt-err" : "cf-opt-hint"}
                aria-invalid={!!errors.options}
                className={cn(
                  "w-full rounded border border-input bg-background px-3 py-2 text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  "resize-y",
                )}
              />
              <p id="cf-opt-hint" className="mt-1 text-xs text-muted-foreground">
                Каждый вариант — отдельная строка или через запятую
              </p>
              {errors.options && (
                <p id="cf-opt-err" role="alert" className="mt-1 text-xs text-destructive">
                  {errors.options.message}
                </p>
              )}
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <Button type="submit" disabled={isSubmitting} data-testid="add-field-submit">
              Добавить
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

// ── DeleteFieldConfirm ─────────────────────────────────────────────────────────

function DeleteFieldConfirm({
  field,
  onConfirm,
  onCancel,
}: {
  field: CustomField;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialogRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onCancel]);

  return (
    <>
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        aria-hidden="true"
        onClick={onCancel}
      />
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="del-field-title"
        aria-describedby="del-field-desc"
        tabIndex={-1}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-sm -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border bg-card shadow-xl focus:outline-none p-6",
        )}
        data-testid="delete-field-confirm"
      >
        <h2 id="del-field-title" className="text-base font-semibold mb-3">
          Удалить поле?
        </h2>
        <p id="del-field-desc" className="text-sm text-muted-foreground mb-5">
          Поле «{field.display_name}» будет удалено из существующих документов этого типа. Эти
          данные будут потеряны навсегда. Продолжить?
        </p>
        <div className="flex gap-2">
          <Button variant="destructive" onClick={onConfirm} data-testid="delete-field-confirm-btn">
            Удалить
          </Button>
          <Button variant="outline" onClick={onCancel}>
            Отмена
          </Button>
        </div>
      </div>
    </>
  );
}
