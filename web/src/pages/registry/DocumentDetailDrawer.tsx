// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * DocumentDetailDrawer — side drawer that opens on row single-click.
 *
 * Tabs: Основное | Вложения | История изменений
 * Keyboard: Esc closes, focus is trapped inside, restored on close.
 * Click outside (the backdrop): closes.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { format, parseISO } from "date-fns";
import { ru } from "date-fns/locale";
import {
  AlertCircle,
  Archive,
  CheckCircle,
  Clock,
  Download,
  FileText,
  Pencil,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useAuth } from "@/features/auth/AuthProvider";
import { downloadAttachment } from "@/features/registry/api";
import { computeStatus } from "@/features/registry/computeStatus";
import {
  ALLOWED_MIME_TYPES,
  useAttachments,
  useDeleteAttachment,
  useUploadAttachment,
} from "@/features/registry/hooks/useAttachments";
import { useActiveAssets } from "@/features/registry/hooks/useAssets";
import {
  usePatchDocument,
  useRestoreDocument,
} from "@/features/registry/hooks/useDocumentMutations";
import { useDocumentTypes } from "@/features/registry/hooks/useDocumentTypes";
import { useDocumentHistory } from "@/features/registry/hooks/useHistory";
import type {
  AuditEvent,
  Document,
  PatchDocumentPayload,
} from "@/features/registry/types";
import type { CustomField } from "@/features/admin/document-types/custom-fields-api";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";
import { StatusBadge } from "@/shared/ui/status-badge";
import { toast } from "@/shared/ui/toast";

// ── Tab types ─────────────────────────────────────────────────────────────────

type DrawerTab = "main" | "attachments" | "history";

// ── Edit form schema ──────────────────────────────────────────────────────────

/**
 * v1.25.0 — full edit scope.
 *
 * The form now accepts ALL editable fields including asset_id, type_code,
 * responsible_user_id (radio: keep/me/unassigned), and custom_field_values
 * (typed inputs driven by the current document type's custom_field_schema).
 *
 * cf values are stored in a single `cf` object keyed by field.key — the
 * payload built on submit converts them to PatchDocumentPayload.custom_field_values.
 *
 * Responsible-user picker is a 3-way radio rather than a full typeahead to
 * avoid introducing a new editor-accessible /users endpoint just for this
 * release; users can still be assigned via «Я» / «Не назначен» / «Без изменений».
 */
const editSchema = z.object({
  asset_id: z.string().min(1, "Выберите компанию"),
  type_code: z.string().min(1, "Выберите тип документа"),
  number: z.string().min(1, "Обязательное поле"),
  issue_date: z.string().nullable().optional(),
  expiry_date: z.string().nullable().optional(),
  responsible_choice: z.enum(["keep", "me", "unassigned"]),
  notes: z.string().max(10000, "Максимум 10 000 символов").nullable().optional(),
  cf: z.record(z.string(), z.string().nullable().optional()).optional(),
});

type EditFormValues = z.infer<typeof editSchema>;

// ── Drawer ────────────────────────────────────────────────────────────────────

interface DocumentDetailDrawerProps {
  document: Document | null;
  onClose: () => void;
  /** Open the drawer directly in edit mode (double-click on row, US-fitts-law). */
  initialEdit?: boolean;
}

export function DocumentDetailDrawer({
  document: doc,
  onClose,
  initialEdit = false,
}: DocumentDetailDrawerProps) {
  const { claims } = useAuth();
  const [activeTab, setActiveTab] = React.useState<DrawerTab>("main");
  const [isEditing, setIsEditing] = React.useState(false);
  const drawerRef = React.useRef<HTMLDivElement>(null);
  const closeButtonRef = React.useRef<HTMLButtonElement>(null);
  const editButtonRef = React.useRef<HTMLButtonElement>(null);
  const prevFocusRef = React.useRef<HTMLElement | null>(null);

  // When a new document opens, reset state and honour initialEdit.
  // (Опускаемся в edit-mode из двойного клика по строке — Fitts'sʼs-law fix:
  // нулевой cursor-travel до CTA.)
  const prevDocIdRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (doc && doc.id !== prevDocIdRef.current) {
      prevDocIdRef.current = doc.id;
      setActiveTab("main");
      setIsEditing(Boolean(initialEdit));
    }
    if (!doc) {
      prevDocIdRef.current = null;
    }
  }, [doc, initialEdit]);

  // Close on Escape; `E` key enters edit mode (US-fitts-law shortcut).
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      // `E` → edit mode, but only when drawer is open, user has edit
      // rights, not already editing, and focus is NOT inside an input
      // (otherwise we would block legitimate typing of the letter E).
      if (!doc) return;
      if (e.key !== "e" && e.key !== "E" && e.key !== "у" && e.key !== "У") return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) {
        return;
      }
      const isArchived = doc.deleted_at !== null;
      const canEdit = (claims?.role === "editor" || claims?.role === "admin") && !isArchived;
      if (!canEdit || isEditing) return;
      setActiveTab("main");
      setIsEditing(true);
      e.preventDefault();
    };
    if (doc) {
      document.addEventListener("keydown", handler);
      // Remember what was focused before opening
      prevFocusRef.current = document.activeElement as HTMLElement;
      // Focus the Edit CTA when present, fall back to close button.
      requestAnimationFrame(() => {
        (editButtonRef.current ?? closeButtonRef.current)?.focus();
      });
    }
    return () => document.removeEventListener("keydown", handler);
  }, [doc, onClose, isEditing, claims?.role]);

  // Restore focus on close
  React.useEffect(() => {
    if (!doc && prevFocusRef.current) {
      prevFocusRef.current.focus();
      prevFocusRef.current = null;
    }
  }, [doc]);

  if (!doc) return null;

  // Always derive display status from expiry_date + deleted_at — same as the
  // registry table. doc.status from the API is the raw DB enum ("active"|"archived"),
  // NOT a display value; using it made the badge fall back to green "ОК" for every
  // document (StatusBadge maps unknown "active" → "ok").
  const computedStatus = computeStatus(doc.expiry_date, doc.deleted_at);
  const isArchived = doc.deleted_at !== null;
  const canEdit = (claims?.role === "editor" || claims?.role === "admin") && !isArchived;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/30" aria-hidden="true" onClick={onClose} />

      {/* Drawer panel */}
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Документ ${doc.number}`}
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col bg-card shadow-xl",
          "border-l border-border",
          "animate-in slide-in-from-right duration-200",
        )}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b p-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-semibold truncate">{doc.number}</h2>
              <StatusBadge status={computedStatus} />
              {isArchived && (
                <span className="inline-flex items-center gap-1 rounded bg-status-archived/10 px-2 py-0.5 text-xs text-status-archived">
                  <Archive className="size-3" aria-hidden />В архиве
                </span>
              )}
            </div>
            <p className="mt-0.5 text-sm text-muted-foreground truncate">{doc.asset_name}</p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label="Закрыть панель"
            className="shrink-0 rounded p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-5" aria-hidden />
          </button>
        </div>

        {/* Action toolbar — primary CTA close to the left edge of the drawer
            (= close to the centre of the viewport on wide monitors), visible
            above the fold so users never have to scroll or reach far right
            to start editing. Fitts's-law fix per UX redesign 2026-05-21. */}
        {canEdit && !isEditing && activeTab === "main" && (
          <div className="flex items-center gap-2 border-b bg-muted/30 px-4 py-2">
            <Button
              ref={editButtonRef}
              type="button"
              size="sm"
              onClick={() => setIsEditing(true)}
              aria-label="Редактировать документ"
              aria-keyshortcuts="E"
              title="Редактировать (E)"
            >
              <Pencil className="size-4 mr-1.5" aria-hidden />
              Редактировать
            </Button>
            <span className="text-xs text-muted-foreground ml-auto select-none">
              Совет: <kbd className="px-1.5 py-0.5 rounded border bg-background font-mono">E</kbd> —
              быстрое переключение в режим правки. В таблице двойной клик по ячейке включает
              inline-редактирование одного поля.
            </span>
          </div>
        )}

        {/* Tab bar */}
        <div role="tablist" aria-label="Разделы документа" className="flex border-b px-4">
          {(["main", "attachments", "history"] as DrawerTab[]).map((tab) => {
            const labels: Record<DrawerTab, string> = {
              main: "Основное",
              attachments: "Вложения",
              history: "История изменений",
            };
            return (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={activeTab === tab}
                aria-controls={`drawer-panel-${tab}`}
                onClick={() => setActiveTab(tab)}
                className={cn(
                  "px-3 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors",
                  activeTab === tab
                    ? "border-primary text-primary"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-t",
                )}
              >
                {labels[tab]}
              </button>
            );
          })}
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto">
          <div
            id="drawer-panel-main"
            role="tabpanel"
            aria-labelledby="tab-main"
            hidden={activeTab !== "main"}
          >
            {activeTab === "main" && (
              <MainTab
                doc={doc}
                isArchived={isArchived}
                isEditing={isEditing}
                setIsEditing={setIsEditing}
              />
            )}
          </div>
          <div
            id="drawer-panel-attachments"
            role="tabpanel"
            aria-labelledby="tab-attachments"
            hidden={activeTab !== "attachments"}
          >
            {activeTab === "attachments" && (
              <AttachmentsTab documentId={doc.id} isArchived={isArchived} />
            )}
          </div>
          <div
            id="drawer-panel-history"
            role="tabpanel"
            aria-labelledby="tab-history"
            hidden={activeTab !== "history"}
          >
            {activeTab === "history" && <HistoryTab documentId={doc.id} />}
          </div>
        </div>
      </div>
    </>
  );
}

// ── Main tab ──────────────────────────────────────────────────────────────────

function MainTab({
  doc,
  isArchived,
  isEditing,
  setIsEditing,
}: {
  doc: Document;
  isArchived: boolean;
  isEditing: boolean;
  setIsEditing: (v: boolean) => void;
}) {
  const { claims } = useAuth();
  // NOTE: `canEdit` lives at the drawer level now (header CTA) — MainTab only needs `canRestore`.
  const canRestore = claims?.role === "admin" && isArchived;
  const patchMutation = usePatchDocument();
  const restoreMutation = useRestoreDocument();
  const { data: assetsData, isLoading: assetsLoading } = useActiveAssets();
  const { data: docTypes, isLoading: typesLoading } = useDocumentTypes();

  // Build initial cf-values for the form: stringify all schema-declared keys for
  // current type. Missing keys default to "" so React-Hook-Form has a defined
  // value to track (avoids uncontrolled→controlled warnings).
  const currentType = React.useMemo(
    () => docTypes?.find((t) => t.code === doc.type_code),
    [docTypes, doc.type_code],
  );
  const initialCfValues = React.useMemo(() => {
    const cf: Record<string, string> = {};
    const schema = currentType?.custom_field_schema ?? [];
    for (const field of schema) {
      const raw = doc.custom_field_values?.[field.key];
      cf[field.key] = raw === null || raw === undefined ? "" : String(raw);
    }
    return cf;
  }, [currentType, doc.custom_field_values]);

  const {
    register,
    handleSubmit,
    reset,
    watch,
    setValue,
    formState: { errors },
  } = useForm<EditFormValues>({
    resolver: zodResolver(editSchema),
    defaultValues: {
      asset_id: doc.asset_id,
      type_code: doc.type_code,
      number: doc.number,
      issue_date: doc.issue_date,
      expiry_date: doc.expiry_date,
      responsible_choice: "keep",
      notes: doc.notes,
      cf: initialCfValues,
    },
  });

  // When type_code changes mid-edit, swap the cf-section to the new type's schema
  // and reseed cf values: keys that exist on both types keep their current value,
  // new keys start blank, removed keys are dropped (silently — same policy as
  // backend cleanup). The user sees the new field set immediately.
  const watchedTypeCode = watch("type_code");
  const newType = React.useMemo(
    () => docTypes?.find((t) => t.code === watchedTypeCode),
    [docTypes, watchedTypeCode],
  );
  const prevWatchedTypeRef = React.useRef(doc.type_code);
  React.useEffect(() => {
    if (!isEditing) return;
    if (watchedTypeCode === prevWatchedTypeRef.current) return;
    prevWatchedTypeRef.current = watchedTypeCode;
    const newSchema = newType?.custom_field_schema ?? [];
    const reseeded: Record<string, string> = {};
    const prevCf = watch("cf") ?? {};
    for (const field of newSchema) {
      const carry = prevCf[field.key];
      reseeded[field.key] = carry === undefined || carry === null ? "" : String(carry);
    }
    setValue("cf", reseeded, { shouldDirty: true });
  }, [watchedTypeCode, newType, isEditing, watch, setValue]);

  const cfSchema = newType?.custom_field_schema ?? currentType?.custom_field_schema ?? [];
  const typeIsChanging = watchedTypeCode !== doc.type_code;

  const onSubmit = handleSubmit(async (values) => {
    const payload: PatchDocumentPayload = {
      number: values.number,
    };
    if (values.asset_id && values.asset_id !== doc.asset_id) {
      payload.asset_id = values.asset_id;
    }
    if (values.type_code && values.type_code !== doc.type_code) {
      payload.type_code = values.type_code;
    }
    if (values.issue_date !== undefined && values.issue_date !== doc.issue_date) {
      payload.issue_date = values.issue_date || null;
    }
    if (values.expiry_date !== undefined && values.expiry_date !== doc.expiry_date) {
      payload.expiry_date = values.expiry_date || null;
    }
    if (values.notes !== undefined && values.notes !== doc.notes) {
      payload.notes = values.notes;
    }
    if (values.responsible_choice === "me") {
      payload.responsible_user_id = claims?.sub ?? null;
    } else if (values.responsible_choice === "unassigned") {
      payload.responsible_user_id = null;
    }

    // Custom field values: only submit if cf-section is visible (schema exists)
    // and differs from the original. Convert empty strings to null so backend
    // stores explicit nulls rather than spurious "" strings.
    if (cfSchema.length > 0 && values.cf) {
      const normalized: Record<string, string | number | null> = {};
      for (const field of cfSchema) {
        const v = values.cf[field.key];
        if (v === undefined || v === null || v === "") {
          normalized[field.key] = null;
        } else if (field.type === "number") {
          const n = Number(v);
          normalized[field.key] = Number.isFinite(n) ? n : null;
        } else {
          normalized[field.key] = v;
        }
      }
      const original = doc.custom_field_values ?? {};
      const originalNormalized: Record<string, string | number | null> = {};
      for (const field of cfSchema) {
        const v = original[field.key];
        originalNormalized[field.key] = v === undefined ? null : v;
      }
      // Submit if any cf value changed OR type_code changed (server prunes
      // orphan keys but we still want the user-edited values applied).
      const changed =
        typeIsChanging ||
        JSON.stringify(normalized) !== JSON.stringify(originalNormalized);
      if (changed) {
        payload.custom_field_values = normalized;
      }
    }

    // At least one field beyond `number` (always present) must differ — let the
    // user know if nothing actually changed.
    const fieldsBeingSent = Object.keys(payload).filter((k) => k !== "number" || payload.number !== doc.number);
    if (fieldsBeingSent.length === 0 && payload.number === doc.number) {
      // No changes — exit edit mode silently.
      setIsEditing(false);
      return;
    }

    await patchMutation.mutateAsync({ id: doc.id, payload });
    setIsEditing(false);
  });

  const onCancel = () => {
    reset();
    prevWatchedTypeRef.current = doc.type_code;
    setIsEditing(false);
  };

  return (
    <div className="p-4 space-y-4">
      {/* Archived banner */}
      {isArchived && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-md bg-status-archived/10 border border-status-archived/30 px-3 py-2 text-sm text-status-archived"
        >
          <Archive className="size-4 shrink-0" aria-hidden />
          <span>Документ в архиве. Редактирование недоступно.</span>
          {canRestore && (
            <Button
              size="sm"
              variant="outline"
              className="ml-auto shrink-0"
              onClick={() => restoreMutation.mutate(doc.id)}
              disabled={restoreMutation.isPending}
            >
              Восстановить
            </Button>
          )}
        </div>
      )}

      {/* Edit / read mode toggle */}
      {/* Primary «Редактировать» CTA lives in the drawer header toolbar
          (Fitts's-law fix — closer to viewport centre). Save/Отмена inside
          the form below stay where they are. */}

      {isEditing ? (
        <form onSubmit={onSubmit} aria-label="Форма редактирования документа" className="space-y-3">
          {(assetsLoading || typesLoading) && (
            <p className="text-sm text-muted-foreground">Загрузка справочников…</p>
          )}

          <Field label="Компания" error={errors.asset_id?.message}>
            <select
              {...register("asset_id")}
              className={cn(
                "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              {assetsData?.items.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Тип документа" error={errors.type_code?.message}>
            <select
              {...register("type_code")}
              className={cn(
                "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              {docTypes?.map((dt) => (
                <option key={dt.code} value={dt.code}>
                  {dt.display_name}
                </option>
              ))}
            </select>
          </Field>

          {typeIsChanging && (
            <div
              role="alert"
              className="rounded-md bg-status-warning/10 border border-status-warning/30 px-3 py-2 text-xs text-status-warning"
            >
              Смена типа документа. Поля, которых нет в новом типе, будут удалены
              после сохранения.
            </div>
          )}

          <Field label="№ документа" error={errors.number?.message}>
            <Input {...register("number")} aria-describedby="err-number" />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Дата выдачи">
              <Input type="date" {...register("issue_date")} />
            </Field>
            <Field label="Действует до">
              <Input type="date" {...register("expiry_date")} />
            </Field>
          </div>

          {/* Ответственный — 3-way radio: keep / me / unassigned.
              Full typeahead picker requires a non-admin /users endpoint
              (out of scope for v1.25.0). */}
          <fieldset className="space-y-1.5">
            <legend className="block text-sm font-medium mb-1">Ответственный</legend>
            {(
              [
                {
                  value: "keep",
                  label: doc.responsible_user_name
                    ? `Без изменений (${doc.responsible_user_name})`
                    : "Без изменений (не назначен)",
                },
                {
                  value: "me",
                  label: `Я (${claims?.email ?? "текущий пользователь"})`,
                },
                { value: "unassigned", label: "Снять назначение" },
              ] as const
            ).map((opt) => (
              <label key={opt.value} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  value={opt.value}
                  {...register("responsible_choice")}
                  className="size-4"
                />
                {opt.label}
              </label>
            ))}
          </fieldset>

          {/* Custom fields — driven by current (or pending) document type schema. */}
          {cfSchema.length > 0 && (
            <section
              aria-label="Дополнительные поля"
              className="space-y-3 rounded-md border border-border/60 bg-muted/20 p-3"
            >
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Дополнительные поля
              </h3>
              {cfSchema.map((field) => (
                <CustomFieldInput
                  key={field.key}
                  field={field}
                  register={register}
                  fieldNamePrefix="cf"
                />
              ))}
            </section>
          )}

          <Field label="Заметки" error={errors.notes?.message}>
            <textarea
              {...register("notes")}
              rows={4}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </Field>

          <div className="flex gap-2 pt-1">
            <Button type="submit" size="sm" disabled={patchMutation.isPending}>
              {patchMutation.isPending ? "Сохранение..." : "Сохранить"}
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={onCancel}>
              Отмена
            </Button>
          </div>
        </form>
      ) : (
        <dl className="space-y-3">
          <DetailRow label="Компания" value={doc.asset_name} />
          <DetailRow label="Тип" value={doc.type_display_name} />
          <DetailRow label="№ документа" value={doc.number} mono />
          <DetailRow
            label="Дата выдачи"
            value={doc.issue_date ? formatDate(doc.issue_date) : "—"}
          />
          <DetailRow
            label="Действует до"
            value={doc.expiry_date ? formatDate(doc.expiry_date) : "Бессрочно"}
          />
          <DetailRow label="Ответственный" value={doc.responsible_user_name ?? "—"} />
          {cfSchema.length > 0 && doc.custom_field_values && (
            <>
              {cfSchema.map((field) => {
                const raw = doc.custom_field_values?.[field.key];
                const display =
                  raw === null || raw === undefined || raw === ""
                    ? "—"
                    : field.type === "date" && typeof raw === "string"
                      ? formatDate(raw)
                      : String(raw);
                return <DetailRow key={field.key} label={field.display_name} value={display} />;
              })}
            </>
          )}
          <DetailRow label="Заметки" value={doc.notes ?? "—"} multiline />
          <DetailRow label="Создан" value={formatDateTime(doc.created_at)} />
          <DetailRow label="Изменён" value={formatDateTime(doc.updated_at)} />
        </dl>
      )}
    </div>
  );
}

// ── Custom field input ────────────────────────────────────────────────────────

function CustomFieldInput({
  field,
  register,
  fieldNamePrefix,
}: {
  field: CustomField;
  register: ReturnType<typeof useForm<EditFormValues>>["register"];
  fieldNamePrefix: "cf";
}) {
  const name = `${fieldNamePrefix}.${field.key}` as const;
  const id = React.useId();
  const label = `${field.display_name}${field.required ? " *" : ""}`;

  let control: React.ReactNode;
  if (field.type === "date") {
    control = (
      <Input
        id={id}
        type="date"
        {...register(name)}
        aria-required={field.required}
      />
    );
  } else if (field.type === "number") {
    control = (
      <Input
        id={id}
        type="number"
        inputMode="decimal"
        step="any"
        {...register(name)}
        aria-required={field.required}
      />
    );
  } else if (field.type === "enum") {
    control = (
      <select
        id={id}
        {...register(name)}
        aria-required={field.required}
        className={cn(
          "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <option value="">—</option>
        {(field.options ?? []).map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  } else {
    control = <Input id={id} {...register(name)} aria-required={field.required} />;
  }

  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium">
        {label}
      </label>
      {control}
    </div>
  );
}

// ── Attachments tab ───────────────────────────────────────────────────────────

function AttachmentsTab({ documentId, isArchived }: { documentId: string; isArchived: boolean }) {
  const { claims } = useAuth();
  const canEdit = (claims?.role === "editor" || claims?.role === "admin") && !isArchived;
  const { data: attachments, isLoading, isError } = useAttachments(documentId);
  const deleteMutation = useDeleteAttachment(documentId);
  const { uploads, upload, clearDone } = useUploadAttachment();
  const dropRef = React.useRef<HTMLDivElement>(null);
  const [isDragging, setIsDragging] = React.useState(false);

  const handleFiles = (files: FileList | null) => {
    if (!files || !canEdit) return;
    for (const file of Array.from(files)) {
      upload(documentId, file);
    }
  };

  const handleDownload = async (attachmentId: string, _filename: string) => {
    // v1.25.4 — Open in a new tab so the browser decides preview vs download
    // based on the response's Content-Type + Content-Disposition (now `inline`
    // from the new /internal/files handler). PDFs and images preview natively;
    // Office formats still trigger Save, with the original filename intact.
    //
    // Previous implementation set `<a download={filename}>` which forces a
    // Save regardless of MIME, defeating the inline disposition. It also
    // suffered from the un-handled signed-URL path (browser saved SPA HTML as
    // .pdf), which is fixed in v1.25.4 alongside this change.
    try {
      const url = await downloadAttachment(attachmentId);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch {
      toast.show({
        title: "Не удалось открыть файл",
        description: "Попробуйте снова. Если ошибка повторяется, перезагрузите страницу.",
        variant: "destructive",
      });
    }
  };

  if (isLoading) {
    return (
      <div className="p-4 space-y-3">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        Не удалось загрузить вложения. Попробуйте позже.
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      {/* Drop zone */}
      {canEdit && (
        <section
          ref={dropRef}
          aria-label="Загрузка вложений. Перетащите файлы или нажмите для выбора."
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragging(false);
            handleFiles(e.dataTransfer.files);
          }}
          className={cn(
            "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 text-center transition-colors",
            isDragging ? "border-primary bg-primary/5" : "border-border",
          )}
        >
          <Upload className="size-8 text-muted-foreground" aria-hidden />
          <p className="text-sm text-muted-foreground">
            Перетащите файлы сюда или{" "}
            <label className="cursor-pointer text-primary underline-offset-2 hover:underline">
              выберите файл
              <input
                type="file"
                multiple
                className="sr-only"
                accept={ALLOWED_MIME_TYPES.join(",")}
                onChange={(e) => handleFiles(e.target.files)}
              />
            </label>
          </p>
          <p className="text-xs text-muted-foreground">
            PDF, JPEG, PNG, TIFF, DOCX, XLSX · макс. 25 МиБ
          </p>
        </section>
      )}

      {/* In-progress uploads */}
      {uploads.length > 0 && (
        <div className="space-y-2" aria-live="polite">
          {uploads.map((u, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: uploads have no stable id; file.name can collide
            <UploadProgressRow key={`${u.file.name}-${i}`} state={u} />
          ))}
          {uploads.some((u) => u.done || u.error) && (
            <button
              type="button"
              onClick={clearDone}
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
            >
              Очистить завершённые
            </button>
          )}
        </div>
      )}

      {/* Attachment list */}
      {(!attachments || attachments.length === 0) && uploads.length === 0 ? (
        <p className="text-sm text-muted-foreground">Нет вложений</p>
      ) : (
        <ul aria-label="Список вложений" className="space-y-1.5">
          {(attachments ?? []).map((att) => (
            <li key={att.id} className="flex items-center gap-3 rounded-md border px-3 py-2">
              <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{att.original_filename}</p>
                <p className="text-xs text-muted-foreground">{formatBytes(att.size_bytes)}</p>
              </div>
              <button
                type="button"
                onClick={() => void handleDownload(att.id, att.original_filename)}
                aria-label={`Скачать ${att.original_filename}`}
                className="rounded p-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Download className="size-4" aria-hidden />
              </button>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => deleteMutation.mutate(att.id)}
                  aria-label={`Удалить ${att.original_filename}`}
                  className="rounded p-1 text-muted-foreground hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Trash2 className="size-4" aria-hidden />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── History tab ────────────────────────────────────────────────────────────────

/**
 * Human-readable Russian timeline of all changes to a document.
 *
 * For читателей (специалисты, руководители, аудит): ФИО актора, человеческое
 * название поля и значения «до → после», группировка по дню, иконка по типу
 * события для быстрого скана. UUID/коды полей не показываются — для compliance
 * сырые значения остаются в API-ответе (`before`/`after`) и доступны в DevTools.
 *
 * Server returns:
 *   actor_name resolved by web-bff;
 *   before_display/after_display resolved by registry-svc for FK fields;
 *   raw before/after for everything else (numbers, dates, text).
 */

type EventCategory =
  | "created"
  | "updated_field"
  | "archived"
  | "restored"
  | "attachment_uploaded"
  | "attachment_deleted"
  | "unknown";

function classifyEvent(event: AuditEvent): EventCategory {
  if (event.event_type === "registry.document.created.v1") return "created";
  if (event.event_type === "registry.document.archived.v1") return "archived";
  if (event.event_type === "registry.document.restored.v1") return "restored";
  if (event.event_type === "registry.document.updated.v1") {
    if (event.field === "attachments") {
      // Publisher emits {before:null, after:{...}} on upload и {before:{...}, after:null} on delete.
      if (event.before === null && event.after !== null) return "attachment_uploaded";
      if (event.after === null && event.before !== null) return "attachment_deleted";
    }
    return "updated_field";
  }
  return "unknown";
}

function iconForCategory(category: EventCategory): string {
  switch (category) {
    case "created":
      return "📄";
    case "updated_field":
      return "✏️";
    case "archived":
      return "📦";
    case "restored":
      return "♻️";
    case "attachment_uploaded":
      return "📎";
    case "attachment_deleted":
      return "🗑️";
    default:
      return "•";
  }
}

const FIELD_LABEL_RU: Record<string, string> = {
  number: "Номер",
  issue_date: "Дата выдачи",
  expiry_date: "Действ. до",
  responsible_user_id: "Ответственный",
  notes: "Заметки",
  type_code: "Тип документа",
  asset_id: "Компания",
  custom_field_values: "Дополнительные поля",
};

const DATE_FIELDS = new Set(["issue_date", "expiry_date"]);

function fieldLabel(field: string | null | undefined): string {
  if (!field) return "поле";
  return FIELD_LABEL_RU[field] ?? field;
}

function verbForCategory(category: EventCategory, event: AuditEvent): string {
  switch (category) {
    case "created":
      return "создал документ";
    case "archived":
      return "архивировал документ";
    case "restored":
      return "восстановил документ из архива";
    case "attachment_uploaded":
      return "загрузил вложение";
    case "attachment_deleted":
      return "удалил вложение";
    case "updated_field":
      return `изменил «${fieldLabel(event.field)}»`;
    default:
      return event.event_type;
  }
}

function formatRawValue(value: unknown, field: string | null): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") {
    // Defensive: shouldn't reach here after server enrichment.
    try {
      return JSON.stringify(value);
    } catch {
      return "—";
    }
  }
  const s = String(value);
  if (field && DATE_FIELDS.has(field)) {
    try {
      return format(parseISO(s), "d MMMM yyyy", { locale: ru });
    } catch {
      return s;
    }
  }
  return s;
}

function displayValue(value: unknown, displayHint: string | null, field: string | null): string {
  if (displayHint !== null && displayHint !== undefined && displayHint !== "") {
    return displayHint;
  }
  return formatRawValue(value, field);
}

function formatDay(iso: string): string {
  try {
    return format(parseISO(iso), "d MMMM yyyy", { locale: ru });
  } catch {
    return iso;
  }
}

function formatTime(iso: string): string {
  try {
    return format(parseISO(iso), "HH:mm", { locale: ru });
  } catch {
    return iso;
  }
}

function groupByDay(events: AuditEvent[]): Array<{ day: string; events: AuditEvent[] }> {
  const map = new Map<string, AuditEvent[]>();
  for (const event of events) {
    const day = formatDay(event.occurred_at);
    const bucket = map.get(day) ?? [];
    bucket.push(event);
    map.set(day, bucket);
  }
  // Preserve insertion order — server returns newest-first; days arrive newest-first.
  return Array.from(map, ([day, evs]) => ({ day, events: evs }));
}

function HistoryItemRow({ event }: { event: AuditEvent }) {
  const category = classifyEvent(event);
  const icon = iconForCategory(category);
  const verb = verbForCategory(category, event);
  const time = formatTime(event.occurred_at);
  const actor = event.actor_name ?? "Неизвестный пользователь";

  let body: React.ReactNode = null;

  if (category === "updated_field") {
    const before = displayValue(event.before, event.before_display, event.field);
    const after = displayValue(event.after, event.after_display, event.field);
    body = (
      <div className="text-sm">
        <span className="line-through text-muted-foreground break-words">{before}</span>
        <span className="mx-1.5 text-muted-foreground">→</span>
        <span className="break-words">{after}</span>
      </div>
    );
  } else if (category === "attachment_uploaded") {
    body = (
      <div className="text-sm text-muted-foreground break-words">
        Файл:{" "}
        <span className="text-foreground">
          {event.after_display ?? formatRawValue(event.after, event.field)}
        </span>
      </div>
    );
  } else if (category === "attachment_deleted") {
    body = (
      <div className="text-sm text-muted-foreground break-words">
        Файл:{" "}
        <span className="text-foreground">
          {event.before_display ?? formatRawValue(event.before, event.field)}
        </span>
      </div>
    );
  }

  return (
    <li className="flex gap-3">
      <span aria-hidden className="text-lg leading-6 select-none w-6 text-center shrink-0">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-xs text-muted-foreground tabular-nums">{time}</div>
        <div className="text-sm">
          <span className="font-medium">{actor}</span>{" "}
          <span className="text-muted-foreground">{verb}</span>
          {category === "attachment_uploaded" || category === "attachment_deleted" ? null : null}
        </div>
        {body}
      </div>
    </li>
  );
}

function HistoryTab({ documentId }: { documentId: string }) {
  const { data, isLoading, isError, isFetching, refetch } = useDocumentHistory(documentId);

  if (isLoading) {
    return (
      <div className="p-4 space-y-3" role="status" aria-busy="true" aria-label="Загрузка истории">
        {Array.from({ length: 5 }, (_, i) => i).map((i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-4 space-y-3" role="alert">
        <p className="text-sm text-muted-foreground">История изменений временно недоступна.</p>
        <Button variant="outline" size="sm" onClick={() => void refetch()}>
          <RefreshCw className="size-4 mr-2" aria-hidden />
          Повторить
        </Button>
      </div>
    );
  }

  // v1.25.1 — Always-visible refresh + audit-lag hint. The audit pipeline
  // (outbox dispatcher → Redis Stream → audit consumer → DB write) can take
  // 5–60 seconds, so a freshly saved change may not appear immediately.
  // Users press «Обновить» to re-poll without closing the drawer.
  const refreshBar = (
    <div className="flex items-center justify-between gap-2 -mt-1 pb-2 border-b">
      <p className="text-xs text-muted-foreground">
        Изменения появляются в истории через несколько секунд после сохранения.
      </p>
      <Button
        variant="outline"
        size="sm"
        onClick={() => void refetch()}
        disabled={isFetching}
        aria-label="Обновить историю"
      >
        <RefreshCw
          className={cn("size-4 mr-2", isFetching && "animate-spin")}
          aria-hidden
        />
        Обновить
      </Button>
    </div>
  );

  if (!data || data.length === 0) {
    return (
      <div className="p-4 space-y-4">
        {refreshBar}
        <p className="text-sm text-muted-foreground">Действий по документу пока не было.</p>
      </div>
    );
  }

  const groups = groupByDay(data);

  return (
    <div className="p-4 space-y-6" aria-label="История изменений документа">
      {refreshBar}
      {groups.map(({ day, events }) => (
        <section key={day}>
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-3">
            {day}
          </h3>
          <ol className="space-y-3">
            {events.map((event) => (
              <HistoryItemRow key={event.id} event={event} />
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function Field({
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
      {React.cloneElement(
        children as React.ReactElement<React.InputHTMLAttributes<HTMLInputElement>>,
        {
          id,
          ...(error ? { "aria-describedby": `${id}-error` } : {}),
          "aria-invalid": !!error,
        },
      )}
      {error && (
        <p id={`${id}-error`} className="mt-1 text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function DetailRow({
  label,
  value,
  mono = false,
  multiline = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
  multiline?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className={cn("mt-0.5 text-sm", mono && "font-mono", multiline && "whitespace-pre-wrap")}>
        {value}
      </dd>
    </div>
  );
}

function UploadProgressRow({
  state,
}: {
  state: import("@/features/registry/hooks/useAttachments").UploadState;
}) {
  const pct =
    state.progress && state.progress.total > 0
      ? Math.round((state.progress.loaded / state.progress.total) * 100)
      : 0;

  return (
    <div className="rounded-md border px-3 py-2 space-y-1">
      <div className="flex items-center gap-2">
        <span className="truncate text-sm flex-1">{state.file.name}</span>
        {state.done && (
          <CheckCircle className="size-4 text-status-ok shrink-0" aria-label="Загружено" />
        )}
        {state.error && (
          <AlertCircle className="size-4 text-destructive shrink-0" aria-label="Ошибка" />
        )}
        {!state.done && !state.error && (
          <Clock
            className="size-4 text-muted-foreground shrink-0 animate-spin"
            aria-label="Загрузка"
          />
        )}
      </div>
      {!state.done && !state.error && (
        <div
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Загрузка ${state.file.name}`}
          className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
        >
          <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
      )}
      {state.error && <p className="text-xs text-destructive">{state.error}</p>}
    </div>
  );
}

// ── Utilities ──────────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  try {
    return format(parseISO(iso), "dd.MM.yyyy", { locale: ru });
  } catch {
    return iso;
  }
}

function formatDateTime(iso: string): string {
  try {
    return format(parseISO(iso), "dd.MM.yyyy HH:mm", { locale: ru });
  } catch {
    return iso;
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}
