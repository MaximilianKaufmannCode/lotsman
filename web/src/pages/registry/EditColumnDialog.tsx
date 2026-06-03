// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * EditColumnDialog — admin-only column rename + (for custom fields) type change.
 *
 * Standard columns: edit display label only (per-tenant override stored in
 * registry.tenant_preferences via column_labels endpoint).
 * Custom (cf_*) columns: edit both display name AND type.
 *   - Label change goes through column_labels (cosmetic).
 *   - Type change goes through PUT /admin/document-types/{code}/custom-fields
 *     which requires re-MFA (TOTP) — backend also blocks the change if any
 *     document already carries a value for the field (avoids data corruption).
 */

import * as React from "react";
import {
  CustomFieldApiResponseError,
  type FieldType,
  updateCustomFieldSchema,
} from "@/features/admin/document-types/custom-fields-api";
import { useUpdateColumnLabels } from "@/features/registry/hooks/useColumnLabels";
import { useDocumentTypes } from "@/features/registry/hooks/useDocumentTypes";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { toast } from "@/shared/ui/toast";

const FIELD_TYPE_LABELS: Record<FieldType, string> = {
  text: "Текст",
  number: "Число",
  date: "Дата",
  enum: "Список",
};

interface Props {
  open: boolean;
  /** Column id being edited. Pass null when closed. */
  columnId: string | null;
  /** Current effective label (after override). */
  currentLabel: string;
  /** True for cf_* columns (custom field). */
  isCustom: boolean;
  /** Current field type for custom columns. Undefined for standard. */
  currentType?: FieldType | undefined;
  onClose: () => void;
}

export function EditColumnDialog({
  open,
  columnId,
  currentLabel,
  isCustom,
  currentType,
  onClose,
}: Props) {
  const updateLabels = useUpdateColumnLabels();
  const { data: docTypes } = useDocumentTypes();

  const [label, setLabel] = React.useState(currentLabel);
  const [type, setType] = React.useState<FieldType>(currentType ?? "text");
  const [totp, setTotp] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  // Reset on open.
  React.useEffect(() => {
    if (open) {
      setLabel(currentLabel);
      setType(currentType ?? "text");
      setTotp("");
      setError(null);
    }
  }, [open, currentLabel, currentType]);

  const labelChanged = label.trim() !== currentLabel.trim() && label.trim().length > 0;
  const typeChanged = isCustom && type !== currentType;
  const needsTotp = typeChanged;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!columnId) return;
    if (!labelChanged && !typeChanged) {
      onClose();
      return;
    }
    if (needsTotp && totp.length !== 6) {
      setError("Введите 6-значный код подтверждения");
      return;
    }

    setBusy(true);
    setError(null);

    try {
      // Type change must run first — if it's blocked (existing data) we
      // shouldn't have committed a label change either.
      if (typeChanged && isCustom) {
        // Find which type owns this field — it's a cf_* column id stripped of
        // the prefix, so look up across all types.
        const fieldKey = columnId.replace(/^cf_/, "");
        const owners = (docTypes ?? []).filter((dt) =>
          (dt.custom_field_schema ?? []).some((f) => f.key === fieldKey),
        );
        for (const dt of owners) {
          const schema = dt.custom_field_schema ?? [];
          const next = schema.map((f) =>
            f.key === fieldKey
              ? {
                  ...f,
                  type,
                  // Drop options when leaving enum, keep current options
                  // when entering enum (admin should add via the
                  // dedicated custom-fields page if going to enum from
                  // scratch).
                  options: type === "enum" ? (f.options ?? null) : null,
                  display_name: labelChanged ? label.trim() : f.display_name,
                }
              : f,
          );
          await updateCustomFieldSchema(dt.code, next, totp);
        }
      }

      if (labelChanged) {
        // Label override — applies to ALL tables that show this column id.
        // Server merges with existing labels so we just send {[id]: label}.
        await updateLabels.mutateAsync({ [columnId]: label.trim() });
      }

      onClose();
    } catch (err) {
      let msg = "Не удалось сохранить изменения";
      if (err instanceof CustomFieldApiResponseError) {
        if (err.code === "REMFA_REPLAY") {
          msg = "Этот код TOTP уже использован — введите свежий";
          setTotp("");
        } else if (err.code === "CUSTOM_FIELD_VALIDATION") {
          msg = err.detail;
        } else {
          msg = err.detail;
        }
      } else if (err instanceof Error) {
        msg = err.message;
      }
      setError(msg);
      toast.show({ title: msg, variant: "destructive" });
    } finally {
      setBusy(false);
    }
  }

  if (!columnId) return null;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Редактировать колонку"
      description={
        isCustom
          ? "Можно поменять название и тип данных. Смена типа требует подтверждения и не пройдёт если в этом поле уже есть значения у документов."
          : "Можно поменять название колонки. Тип данных стандартных колонок не меняется."
      }
    >
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        <div>
          <Label htmlFor="ec-label">Название колонки</Label>
          <Input
            id="ec-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={100}
            autoFocus
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Будет видно всем пользователям. Очистите поле и сохраните, чтобы вернуться к названию по
            умолчанию.
          </p>
        </div>

        {isCustom && (
          <div>
            <Label htmlFor="ec-type">Тип данных</Label>
            <select
              id="ec-type"
              value={type}
              onChange={(e) => setType(e.target.value as FieldType)}
              className="mt-1 w-full rounded border border-input bg-background px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring h-9"
            >
              {(["text", "number", "date", "enum"] as const).map((ft) => (
                <option key={ft} value={ft}>
                  {FIELD_TYPE_LABELS[ft]}
                </option>
              ))}
            </select>
            {typeChanged && (
              <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                Смена типа: backend откажет, если хотя бы один документ уже хранит значение этого
                поля. Сначала очистите значения.
              </p>
            )}
          </div>
        )}

        {needsTotp && (
          <div>
            <Label htmlFor="ec-totp">Код подтверждения</Label>
            <Input
              id="ec-totp"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              placeholder="123456"
              value={totp}
              onChange={(e) => setTotp(e.target.value.replace(/\D/g, "").slice(0, 6))}
              className="font-mono text-center"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              6-значный код из приложения-аутентификатора.
            </p>
          </div>
        )}

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button type="submit" disabled={busy || (!labelChanged && !typeChanged)} aria-busy={busy}>
            {busy ? "Сохранение…" : "Сохранить"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
