// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ImportXlsxDialog — 2-step import wizard.
 *
 * Step 1: Upload .xlsx/.xlsm → POST /admin/import/preview
 * Step 2: Review decisions for unknown columns + TOTP → POST /admin/import/confirm
 *
 * US-8: smart import with custom field mapping.
 * Replaces the previous 1-step import (Phase 2 of the flexible-document-fields feature).
 *
 * Session TTL is handled server-side (Redis); closing the dialog without
 * completing just lets the session expire — no explicit DELETE needed.
 */

import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileWarning, Info } from "lucide-react";
import * as React from "react";
import {
  CustomFieldApiResponseError,
  type FieldType,
  type ImportConfirmResponse,
  type ImportDecision,
  type ImportPreviewResponse,
  importConfirm,
  importPreview,
  type UnknownColumn,
} from "@/features/admin/document-types/custom-fields-api";
import { useDocumentTypes } from "@/features/registry/hooks/useDocumentTypes";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { toast } from "@/shared/ui/toast";

// ── Types for local reducer state ─────────────────────────────────────────────

type ActionChoice = "create_new" | "map_to_existing" | "skip";

interface DecisionState {
  header: string;
  action: ActionChoice;
  // create_new / rename
  new_key: string;
  target_type: string;
  field_type: FieldType;
  display_name: string;
  options_raw: string; // newline/comma separated
  /** When true, the new field is added to every document type that appears
   * in this import — overrides target_type. */
  apply_to_all_types: boolean;
  // map_to_existing
  mapped_to_field: string;
  map_target_type: string;
}

type DecisionsMap = Record<string, DecisionState>; // keyed by header

interface WizardState {
  step: 1 | 2;
  busy: boolean;
  errorMsg: string | null;
  preview: ImportPreviewResponse | null;
  decisions: DecisionsMap;
  totpValue: string;
  totpError: string | null;
  result: ImportConfirmResponse | null;
}

type WizardAction =
  | { type: "START_UPLOAD" }
  | { type: "UPLOAD_OK"; preview: ImportPreviewResponse }
  | { type: "UPLOAD_ERR"; msg: string }
  | { type: "SET_DECISION"; header: string; patch: Partial<DecisionState> }
  | { type: "SET_TOTP"; value: string }
  | { type: "SET_TOTP_ERROR"; msg: string | null }
  | { type: "START_CONFIRM" }
  | { type: "CONFIRM_OK"; result: ImportConfirmResponse }
  | { type: "CONFIRM_ERR"; msg: string }
  | { type: "RESET" };

function initialState(): WizardState {
  return {
    step: 1,
    busy: false,
    errorMsg: null,
    preview: null,
    decisions: {},
    totpValue: "",
    totpError: null,
    result: null,
  };
}

// GOST 7.79-2000 «System B» Cyrillic → Latin transliteration, lower-case only.
// Used to derive a default `new_key` from Cyrillic Excel headers so that
// auto-generated keys are unique and human-readable (e.g. «Активность» → `aktivnost`)
// rather than collapsing to an empty stem like `f_`.
const _CYR_TO_LAT: Record<string, string> = {
  а: "a",
  б: "b",
  в: "v",
  г: "g",
  д: "d",
  е: "e",
  ё: "yo",
  ж: "zh",
  з: "z",
  и: "i",
  й: "j",
  к: "k",
  л: "l",
  м: "m",
  н: "n",
  о: "o",
  п: "p",
  р: "r",
  с: "s",
  т: "t",
  у: "u",
  ф: "f",
  х: "h",
  ц: "c",
  ч: "ch",
  ш: "sh",
  щ: "sch",
  ъ: "",
  ы: "y",
  ь: "",
  э: "e",
  ю: "yu",
  я: "ya",
};

function _slugifyKey(header: string): string {
  const transliterated = header
    .toLowerCase()
    .split("")
    .map((ch) => _CYR_TO_LAT[ch] ?? ch)
    .join("");
  const base = transliterated
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_")
    .slice(0, 60);
  if (!base) return "field";
  return /^[a-z]/.test(base) ? base : `f_${base}`;
}

function initDecisions(unknownColumns: UnknownColumn[]): DecisionsMap {
  // Ensure key uniqueness: if the slug collides with one already chosen for
  // an earlier column, append _2, _3, … until it is unique. Without this two
  // similarly-named headers (or Cyrillic headers transliterating to the same
  // base) would silently overwrite each other's schema entry on confirm.
  const usedKeys = new Set<string>();
  const ensureUnique = (k: string): string => {
    if (!usedKeys.has(k)) {
      usedKeys.add(k);
      return k;
    }
    let i = 2;
    while (usedKeys.has(`${k}_${i}`)) i += 1;
    const next = `${k}_${i}`;
    usedKeys.add(next);
    return next;
  };
  return Object.fromEntries(
    unknownColumns.map((col) => [
      col.header,
      {
        header: col.header,
        action: "skip" as ActionChoice,
        new_key: ensureUnique(_slugifyKey(col.header)),
        target_type: "",
        field_type: col.suggested_type,
        display_name: col.header,
        options_raw: "",
        apply_to_all_types: true,
        mapped_to_field: "",
        map_target_type: "",
      } satisfies DecisionState,
    ]),
  );
}

function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case "START_UPLOAD":
      return { ...state, busy: true, errorMsg: null };

    case "UPLOAD_OK":
      return {
        ...state,
        busy: false,
        step: 2,
        preview: action.preview,
        decisions: initDecisions(action.preview.unknown_columns),
        errorMsg: null,
      };

    case "UPLOAD_ERR":
      return { ...state, busy: false, errorMsg: action.msg };

    case "SET_DECISION": {
      const existing = state.decisions[action.header];
      if (!existing) return state;
      const merged: DecisionState = { ...existing, ...action.patch };
      return {
        ...state,
        decisions: {
          ...state.decisions,
          [action.header]: merged,
        },
      };
    }

    case "SET_TOTP":
      return { ...state, totpValue: action.value, totpError: null };

    case "SET_TOTP_ERROR":
      return { ...state, totpError: action.msg };

    case "START_CONFIRM":
      return { ...state, busy: true, totpError: null };

    case "CONFIRM_OK":
      return { ...state, busy: false, result: action.result };

    case "CONFIRM_ERR":
      return { ...state, busy: false };

    case "RESET":
      return initialState();

    default:
      return state;
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const FIELD_TYPE_LABELS: Record<FieldType, string> = {
  text: "Текст",
  number: "Число",
  date: "Дата",
  enum: "Список",
};

const FIELD_TYPE_ICON: Record<FieldType, string> = {
  text: "T",
  number: "#",
  date: "📅",
  enum: "≡",
};

function parseOptions(raw: string): string[] {
  return raw
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function buildDecision(ds: DecisionState): ImportDecision {
  if (ds.action === "skip") {
    return { header: ds.header, action: "skip" };
  }
  if (ds.action === "map_to_existing") {
    return {
      header: ds.header,
      action: "map_to_existing",
      mapped_to_field: ds.mapped_to_field,
      target_type: ds.map_target_type,
    };
  }
  // create_new
  const base = {
    header: ds.header,
    action: "create_new" as const,
    new_key: ds.new_key,
    // When apply_to_all_types=true the backend ignores target_type and
    // expands the field to every type present in the import. Send a
    // sentinel value so backend's pattern validation passes.
    target_type: ds.apply_to_all_types ? "_all_" : ds.target_type,
    field_type: ds.field_type,
    display_name: ds.display_name,
    apply_to_all_types: ds.apply_to_all_types,
  };
  if (ds.field_type === "enum") {
    return { ...base, options: parseOptions(ds.options_raw) };
  }
  return base;
}

function isDecisionComplete(ds: DecisionState): boolean {
  if (ds.action === "skip") return true;
  if (ds.action === "map_to_existing") {
    return ds.mapped_to_field.length > 0 && ds.map_target_type.length > 0;
  }
  // create_new
  if (!ds.new_key || !ds.field_type) return false;
  if (!ds.apply_to_all_types && !ds.target_type) return false;
  if (ds.field_type === "enum" && parseOptions(ds.options_raw).length === 0) return false;
  return true;
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  open: boolean;
  onClose: () => void;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ImportXlsxDialog({ open, onClose }: Props) {
  const [state, dispatch] = React.useReducer(wizardReducer, undefined, initialState);
  const [file, setFile] = React.useState<File | null>(null);
  const queryClient = useQueryClient();
  const { data: documentTypes } = useDocumentTypes();
  const totpRef = React.useRef<HTMLInputElement>(null);

  const handleClose = () => {
    dispatch({ type: "RESET" });
    setFile(null);
    onClose();
  };

  // ── Step 1: upload ─────────────────────────────────────────────────────────

  async function handleUpload() {
    if (!file) return;
    dispatch({ type: "START_UPLOAD" });
    try {
      const preview = await importPreview(file);
      dispatch({ type: "UPLOAD_OK", preview });
    } catch (err) {
      let msg = "Произошла ошибка при загрузке файла";
      if (err instanceof CustomFieldApiResponseError) {
        if (err.status === 413) {
          msg = "Файл слишком большой (макс 10 МБ)";
        } else if (err.status === 422) {
          msg = "Не удалось разобрать файл — проверьте что первая строка содержит заголовки";
        } else {
          msg = err.detail;
        }
      }
      dispatch({ type: "UPLOAD_ERR", msg });
    }
  }

  // ── Step 2: confirm ────────────────────────────────────────────────────────

  async function handleConfirm() {
    if (!state.preview) return;

    const totp = state.totpValue.trim();
    if (totp.length !== 6 || !/^\d{6}$/.test(totp)) {
      dispatch({ type: "SET_TOTP_ERROR", msg: "Введите 6-значный TOTP-код" });
      requestAnimationFrame(() => totpRef.current?.focus());
      return;
    }

    dispatch({ type: "START_CONFIRM" });

    const decisions: ImportDecision[] = Object.values(state.decisions).map(buildDecision);

    try {
      const result = await importConfirm(state.preview.import_session_id, decisions, totp);
      dispatch({ type: "CONFIRM_OK", result });

      // Invalidate registry and schema queries
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      await queryClient.invalidateQueries({ queryKey: ["assets"] });
      await queryClient.invalidateQueries({ queryKey: ["document-types"] });

      const successMsg =
        result.fields_added > 0
          ? `Импортировано ${result.rows_imported} · Добавлено ${result.fields_added} полей`
          : `Импортировано ${result.rows_imported} строк`;

      toast.show({ title: successMsg, variant: "success" });

      if (result.rows_failed > 0) {
        const errDesc = result.errors
          ?.slice(0, 3)
          .map((e) => `строка ${e.row_index}: ${e.error}`)
          .join("; ");
        toast.show({
          title: `Не удалось импортировать ${result.rows_failed} строк`,
          ...(errDesc ? { description: errDesc } : {}),
          variant: "destructive",
        });
      }
    } catch (err) {
      let msg = "Произошла ошибка при подтверждении импорта";
      if (err instanceof CustomFieldApiResponseError) {
        if (err.code === "REMFA_REPLAY") {
          dispatch({
            type: "SET_TOTP_ERROR",
            msg: "TOTP-код уже использован. Дождитесь следующего (30 с).",
          });
          dispatch({ type: "SET_TOTP", value: "" });
          requestAnimationFrame(() => totpRef.current?.focus());
          dispatch({ type: "CONFIRM_ERR", msg: "" });
          return;
        }
        if (err.status === 410) {
          msg = "Сессия импорта истекла. Загрузите файл заново.";
        } else {
          msg = err.detail;
        }
      }
      dispatch({ type: "CONFIRM_ERR", msg });
      toast.show({ title: "Ошибка импорта", description: msg, variant: "destructive" });
    }
  }

  // ── Derived ────────────────────────────────────────────────────────────────

  const allDecisionsComplete =
    state.preview !== null && Object.values(state.decisions).every(isDecisionComplete);

  const canSubmit =
    allDecisionsComplete &&
    state.totpValue.length === 6 &&
    /^\d{6}$/.test(state.totpValue) &&
    !state.busy;

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      title={state.step === 1 ? "Импорт реестра из Excel" : "Сопоставление колонок"}
      description={
        state.step === 1
          ? "Поддерживаются файлы .xlsx и .xlsm до 10 МБ"
          : "Укажите действия для нераспознанных колонок"
      }
      className="max-w-3xl"
    >
      {/* ── Step 1 ─────────────────────────────────────────────────────────── */}
      {state.step === 1 && !state.result && (
        <div className="space-y-4">
          <div>
            <input
              type="file"
              accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel.sheet.macroEnabled.12"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={state.busy}
              className="block text-sm w-full file:mr-3 file:py-1.5 file:px-3 file:rounded file:border file:border-input file:bg-background hover:file:bg-accent"
              data-testid="import-file-input"
            />
          </div>

          {state.errorMsg && (
            <div className="flex items-start gap-2 text-sm text-destructive border border-destructive/30 bg-destructive/5 rounded p-3">
              <FileWarning className="size-4 mt-0.5 shrink-0" aria-hidden />
              {state.errorMsg}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={handleClose} disabled={state.busy}>
              Отмена
            </Button>
            <Button
              onClick={() => void handleUpload()}
              disabled={!file || state.busy}
              data-testid="import-upload-btn"
            >
              {state.busy ? "Загрузка..." : "Загрузить и проверить"}
            </Button>
          </div>
        </div>
      )}

      {/* ── Step 2 ─────────────────────────────────────────────────────────── */}
      {state.step === 2 && state.preview && !state.result && (
        <div className="space-y-5">
          {/* Known columns summary */}
          {state.preview.known_columns.length > 0 && (
            <div className="flex items-start gap-2 rounded bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 px-3 py-2.5 text-sm">
              <CheckCircle2
                className="size-4 mt-0.5 text-green-600 dark:text-green-400 shrink-0"
                aria-hidden
              />
              <span>
                <span className="font-medium">Распознано: </span>
                {state.preview.known_columns.map((c) => c.header).join(", ")}
                <span className="text-muted-foreground ml-2">
                  · {state.preview.rows_total} строк
                </span>
              </span>
            </div>
          )}

          {/* Unknown columns */}
          {state.preview.unknown_columns.length === 0 ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Info className="size-4 shrink-0" aria-hidden />
              Все колонки распознаны. Можно импортировать без дополнительных действий.
            </div>
          ) : (
            <div className="space-y-4">
              <p className="text-sm font-medium">
                Нераспознанных колонок: {state.preview.unknown_columns.length}
              </p>
              {state.preview.unknown_columns.map((col) => {
                const ds = state.decisions[col.header];
                if (!ds) return null;
                return (
                  <UnknownColumnCard
                    key={col.header}
                    col={col}
                    ds={ds}
                    documentTypes={documentTypes ?? []}
                    onChange={(patch) =>
                      dispatch({ type: "SET_DECISION", header: col.header, patch })
                    }
                  />
                );
              })}
            </div>
          )}

          {/* TOTP */}
          <div className="border-t pt-4 space-y-2">
            <label htmlFor="import-totp" className="block text-sm font-medium">
              Код TOTP для подтверждения импорта
            </label>
            <Input
              id="import-totp"
              ref={totpRef}
              type="text"
              inputMode="numeric"
              maxLength={6}
              pattern="\d{6}"
              placeholder="123456"
              value={state.totpValue}
              onChange={(e) =>
                dispatch({
                  type: "SET_TOTP",
                  value: e.target.value.replace(/\D/g, "").slice(0, 6),
                })
              }
              aria-describedby={state.totpError ? "import-totp-err" : "import-totp-hint"}
              aria-invalid={!!state.totpError}
              disabled={state.busy}
              className="w-36"
              data-testid="import-totp-input"
            />
            <p id="import-totp-hint" className="text-xs text-muted-foreground">
              6-значный код из вашего приложения-аутентификатора
            </p>
            {state.totpError && (
              <p
                id="import-totp-err"
                role="alert"
                aria-live="polite"
                className="text-xs text-destructive"
              >
                {state.totpError}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={handleClose} disabled={state.busy}>
              Отмена
            </Button>
            <Button
              onClick={() => void handleConfirm()}
              disabled={!canSubmit}
              data-testid="import-confirm-btn"
            >
              {state.busy ? "Импорт..." : "Импортировать"}
            </Button>
          </div>
        </div>
      )}

      {/* ── Done ───────────────────────────────────────────────────────────── */}
      {state.result && (
        <div className="space-y-4">
          <div className="flex items-start gap-2 rounded bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 px-3 py-3 text-sm">
            <CheckCircle2
              className="size-4 mt-0.5 text-green-600 dark:text-green-400 shrink-0"
              aria-hidden
            />
            <div>
              <p className="font-medium">Импорт завершён</p>
              <p className="text-muted-foreground">
                Импортировано: {state.result.rows_imported} строк
                {state.result.fields_added > 0 &&
                  ` · Добавлено полей: ${state.result.fields_added}`}
                {state.result.rows_failed > 0 && ` · Ошибок: ${state.result.rows_failed}`}
              </p>
            </div>
          </div>

          {state.result.errors && state.result.errors.length > 0 && (
            <details className="text-xs" open={state.result.rows_imported === 0}>
              <summary className="cursor-pointer text-muted-foreground">
                Показать ошибки ({state.result.errors.length})
              </summary>
              <div className="max-h-40 overflow-auto mt-2 space-y-1">
                {state.result.errors.map((e, i) => (
                  <div
                    // biome-ignore lint/suspicious/noArrayIndexKey: error list is append-only, no stable key
                    key={`err-${i}`}
                    className="border-l-2 border-destructive/40 pl-2 text-destructive/80"
                  >
                    <span className="font-medium">Строка {e.row_index}:</span> {e.error}
                  </div>
                ))}
              </div>
            </details>
          )}

          <div className="flex justify-end">
            <Button onClick={handleClose}>Закрыть</Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

// ── UnknownColumnCard ──────────────────────────────────────────────────────────

interface UnknownColumnCardProps {
  col: UnknownColumn;
  ds: DecisionState;
  documentTypes: import("@/features/registry/types").DocumentType[];
  onChange: (patch: Partial<DecisionState>) => void;
}

function UnknownColumnCard({ col, ds, documentTypes, onChange }: UnknownColumnCardProps) {
  const isComplete = isDecisionComplete(ds);

  return (
    <div
      className={cn(
        "rounded-lg border p-4 space-y-3",
        !isComplete && ds.action !== "skip" && "border-status-soon/50 bg-status-soon/5",
      )}
      data-testid={`unknown-col-card-${col.header}`}
    >
      {/* Header row */}
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <p className="font-mono text-sm font-medium">{col.header}</p>
          {col.sample_values.length > 0 && (
            <p className="mt-0.5 text-xs text-muted-foreground italic">
              {col.sample_values.slice(0, 3).join(", ")}
            </p>
          )}
        </div>
        <span
          className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs font-medium"
          title={`Предполагаемый тип: ${FIELD_TYPE_LABELS[col.suggested_type]}`}
        >
          {FIELD_TYPE_ICON[col.suggested_type]} {FIELD_TYPE_LABELS[col.suggested_type]}
        </span>
      </div>

      {/* Action radio group */}
      <fieldset>
        <legend className="text-xs font-medium mb-2">Действие</legend>
        <div className="space-y-1">
          {(
            [
              { value: "create_new", label: "Создать новое поле" },
              { value: "map_to_existing", label: "Сопоставить с существующим" },
              { value: "skip", label: "Пропустить (не импортировать эту колонку)" },
            ] as { value: ActionChoice; label: string }[]
          ).map((opt) => (
            <label key={opt.value} className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="radio"
                name={`action-${col.header}`}
                value={opt.value}
                checked={ds.action === opt.value}
                onChange={() => onChange({ action: opt.value })}
                className="focus-visible:ring-2 focus-visible:ring-ring"
              />
              {opt.label}
            </label>
          ))}
        </div>
      </fieldset>

      {/* Create new fields */}
      {ds.action === "create_new" && (
        <div className="grid grid-cols-2 gap-3 pt-1">
          {/* Display name */}
          <div>
            <label htmlFor={`dn-${col.header}`} className="mb-1 block text-xs font-medium">
              Название поля
            </label>
            <Input
              id={`dn-${col.header}`}
              value={ds.display_name}
              onChange={(e) => onChange({ display_name: e.target.value })}
              placeholder={col.header}
              className="h-8 text-sm"
            />
          </div>

          {/* Key */}
          <div>
            <label htmlFor={`key-${col.header}`} className="mb-1 block text-xs font-medium">
              Ключ
            </label>
            <Input
              id={`key-${col.header}`}
              value={ds.new_key}
              onChange={(e) =>
                onChange({
                  new_key: e.target.value
                    .toLowerCase()
                    .replace(/[^a-z0-9_]/g, "_")
                    .slice(0, 64),
                })
              }
              placeholder="field_key"
              className="h-8 text-sm font-mono"
            />
          </div>

          {/* Type */}
          <div>
            <label htmlFor={`type-${col.header}`} className="mb-1 block text-xs font-medium">
              Тип поля
            </label>
            <select
              id={`type-${col.header}`}
              value={ds.field_type}
              onChange={(e) => onChange({ field_type: e.target.value as FieldType })}
              className="w-full rounded border border-input bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring h-8"
            >
              <option value="text">Текст</option>
              <option value="number">Число</option>
              <option value="date">Дата</option>
              <option value="enum">Список (enum)</option>
            </select>
          </div>

          {/* Target document type */}
          <div className="col-span-2">
            <label className="mb-1.5 flex items-center gap-2 text-xs font-medium cursor-pointer">
              <input
                type="checkbox"
                checked={ds.apply_to_all_types}
                onChange={(e) => onChange({ apply_to_all_types: e.target.checked })}
                className="rounded border-input"
              />
              <span>
                Применить ко всем типам документов в этом импорте{" "}
                <span className="text-muted-foreground font-normal">(рекомендуется)</span>
              </span>
            </label>
            {!ds.apply_to_all_types && (
              <>
                <label
                  htmlFor={`ttype-${col.header}`}
                  className="mb-1 mt-2 block text-xs font-medium"
                >
                  Тип документа
                </label>
                <select
                  id={`ttype-${col.header}`}
                  value={ds.target_type}
                  onChange={(e) => onChange({ target_type: e.target.value })}
                  className="w-full rounded border border-input bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring h-8"
                >
                  <option value="">— выберите —</option>
                  {documentTypes.map((dt) => (
                    <option key={dt.code} value={dt.code}>
                      {dt.display_name}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-400 leading-tight">
                  Поле создаётся ТОЛЬКО на выбранном типе. У документов других типов в этом импорте
                  значения колонки сохранены не будут.
                </p>
              </>
            )}
          </div>

          {/* Enum options */}
          {ds.field_type === "enum" && (
            <div className="col-span-2">
              <label htmlFor={`opts-${col.header}`} className="mb-1 block text-xs font-medium">
                Варианты (один на строку или через запятую)
              </label>
              <textarea
                id={`opts-${col.header}`}
                value={ds.options_raw}
                onChange={(e) => onChange({ options_raw: e.target.value })}
                rows={3}
                className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring resize-y"
                placeholder={"Вариант 1\nВариант 2"}
              />
            </div>
          )}
        </div>
      )}

      {/* Map to existing fields */}
      {ds.action === "map_to_existing" && (
        <div className="grid grid-cols-2 gap-3 pt-1">
          <div>
            <label htmlFor={`mtype-${col.header}`} className="mb-1 block text-xs font-medium">
              Тип документа
            </label>
            <select
              id={`mtype-${col.header}`}
              value={ds.map_target_type}
              onChange={(e) => onChange({ map_target_type: e.target.value, mapped_to_field: "" })}
              className="w-full rounded border border-input bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring h-8"
            >
              <option value="">— выберите —</option>
              {documentTypes.map((dt) => (
                <option key={dt.code} value={dt.code}>
                  {dt.display_name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor={`mfield-${col.header}`} className="mb-1 block text-xs font-medium">
              Поле
            </label>
            <Input
              id={`mfield-${col.header}`}
              value={ds.mapped_to_field}
              onChange={(e) => onChange({ mapped_to_field: e.target.value })}
              placeholder="existing_field_key"
              className="h-8 text-sm font-mono"
              disabled={!ds.map_target_type}
            />
          </div>
        </div>
      )}
    </div>
  );
}
