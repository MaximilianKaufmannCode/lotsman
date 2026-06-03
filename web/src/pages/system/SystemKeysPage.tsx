// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * /system/keys — Key rotation tracking.
 * Table: Key | Last rotated | Days since | Status.
 * Row highlight: red ≥90 days, yellow 75–89 days.
 * Per-row: "Record rotation" → modal with DatePicker + TOTP.
 * No typed-confirmation (recording rotation is not destructive).
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { format } from "date-fns";
import { Loader2 } from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { fetchSystemKeys, recordKeyRotation, SystemApiResponseError } from "@/features/system/api";
import type { KeyEntry } from "@/features/system/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { toast } from "@/shared/ui/toast";

const col = createColumnHelper<KeyEntry>();

// ── Days status badge ─────────────────────────────────────────────────────────

function DaysBadge({ days }: { days: number }) {
  const { t } = useTranslation();
  if (days >= 90) {
    return (
      <span
        className="inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold bg-status-overdue/10 text-status-overdue"
        data-testid="days-badge-red"
      >
        {days} {t("system.keys_days")} — {t("system.keys_rotation_overdue")}
      </span>
    );
  }
  if (days >= 75) {
    return (
      <span
        className="inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold bg-status-soon/10 text-status-soon"
        data-testid="days-badge-yellow"
      >
        {days} {t("system.keys_days")} — {t("system.keys_rotation_soon")}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold bg-status-ok/10 text-status-ok">
      {days} {t("system.keys_days")}
    </span>
  );
}

// ── Record rotation dialog ────────────────────────────────────────────────────

interface RecordRotationDialogProps {
  open: boolean;
  keyEntry: KeyEntry | null;
  onClose: () => void;
  onRecorded: () => void;
}

function RecordRotationDialog({ open, keyEntry, onClose, onRecorded }: RecordRotationDialogProps) {
  const { t } = useTranslation();
  const [rotatedAt, setRotatedAt] = React.useState(() => format(new Date(), "yyyy-MM-dd"));
  const [totp, setTotp] = React.useState("");
  const [totpError, setTotpError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const totpRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (open) {
      setRotatedAt(format(new Date(), "yyyy-MM-dd"));
      setTotp("");
      setTotpError(null);
      setIsSubmitting(false);
    }
  }, [open]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!keyEntry || totp.length !== 6) {
      if (totp.length !== 6) setTotpError(t("system.totp_required_6"));
      return;
    }
    setIsSubmitting(true);
    setTotpError(null);
    try {
      await recordKeyRotation(keyEntry.key_id, {
        totp_code: totp,
        rotated_at: new Date(rotatedAt).toISOString(),
      });
      toast.show({ title: t("system.keys_rotation_recorded"), variant: "success" });
      onRecorded();
      onClose();
    } catch (err) {
      if (
        err instanceof SystemApiResponseError &&
        (err.code === "REMFA_REPLAY" || err.code === "REMFA_REQUIRED")
      ) {
        setTotp("");
        setTotpError(t("system.error_totp_replay"));
        setTimeout(() => totpRef.current?.focus(), 50);
      } else if (err instanceof SystemApiResponseError) {
        setTotpError(err.detail);
      } else {
        setTotpError(t("login_errors.network_error_title"));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("system.keys_record_title")}
      description={
        keyEntry ? t("system.keys_record_description", { key: keyEntry.key_id }) : undefined
      }
    >
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
        {/* Date picker */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="key-rotated-at">{t("system.keys_rotated_at_label")}</Label>
          <Input
            id="key-rotated-at"
            type="date"
            value={rotatedAt}
            max={format(new Date(), "yyyy-MM-dd")}
            onChange={(e) => setRotatedAt(e.target.value)}
          />
        </div>

        {/* TOTP */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="key-totp">{t("system.totp_label")}</Label>
          <Input
            id="key-totp"
            ref={totpRef}
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            placeholder="123456"
            value={totp}
            onChange={(e) => {
              setTotp(e.target.value.replace(/\D/g, "").slice(0, 6));
              setTotpError(null);
            }}
            aria-invalid={totpError ? true : undefined}
            aria-describedby={totpError ? "key-totp-error" : "key-totp-hint"}
            className={cn("font-mono text-center", totpError && "border-destructive")}
          />
          {totpError ? (
            <p id="key-totp-error" role="alert" className="text-xs font-medium text-destructive">
              {totpError}
            </p>
          ) : (
            <p id="key-totp-hint" className="text-xs text-muted-foreground">
              {t("system.totp_hint")}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button
            type="submit"
            disabled={totp.length !== 6 || isSubmitting}
            aria-busy={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {t("system.submitting")}
              </>
            ) : (
              t("system.keys_record_btn")
            )}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function SystemKeysPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [recordTarget, setRecordTarget] = React.useState<KeyEntry | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["system", "keys"],
    queryFn: fetchSystemKeys,
    staleTime: 60_000,
  });

  const getRowCls = (entry: KeyEntry): string => {
    if (entry.days_since >= 90) return "bg-status-overdue/5";
    if (entry.days_since >= 75) return "bg-status-soon/5";
    return "";
  };

  const columns = React.useMemo(
    () => [
      col.accessor("key_id", {
        header: t("system.keys_col_key"),
        cell: (info) => <span className="font-mono text-sm">{info.getValue()}</span>,
      }),
      col.accessor("rotated_at", {
        header: t("system.keys_col_rotated_at"),
        cell: (info) => (
          <span className="text-sm">{format(new Date(info.getValue()), "dd.MM.yyyy HH:mm")}</span>
        ),
      }),
      col.accessor("rotated_by_email", {
        header: t("system.keys_col_rotated_by"),
        cell: (info) => <span className="text-sm">{info.getValue()}</span>,
      }),
      col.accessor("days_since", {
        header: t("system.keys_col_days_since"),
        cell: (info) => <DaysBadge days={info.getValue()} />,
      }),
      col.display({
        id: "actions",
        header: () => <span className="sr-only">{t("admin.col_actions")}</span>,
        cell: ({ row }) => (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setRecordTarget(row.original)}
            className="text-xs"
          >
            {t("system.keys_record_btn")}
          </Button>
        ),
      }),
    ],
    [t],
  );

  const table = useReactTable({
    data: data ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">{t("system.keys_title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("system.keys_subtitle")}</p>
      </div>

      {isError && (
        <div
          role="alert"
          className="rounded bg-destructive/10 border border-destructive px-3 py-2 mb-6"
        >
          <p className="text-sm text-destructive">{t("system.keys_error")}</p>
        </div>
      )}

      {isLoading && (
        <div
          role="status"
          aria-busy="true"
          aria-label={t("common.loading")}
          className="flex justify-center py-12"
        >
          <Loader2 className="h-8 w-8 animate-spin text-primary" aria-hidden />
        </div>
      )}

      {!isLoading && (
        <div className="rounded-md border border-border overflow-auto">
          <table className="w-full text-sm" aria-label={t("system.keys_table_label")}>
            <thead className="bg-muted/50 sticky top-0">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => (
                    <th
                      key={header.id}
                      scope="col"
                      className="px-3 py-2 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap"
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody className="divide-y divide-border">
              {table.getRowModel().rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="px-3 py-8 text-center text-sm text-muted-foreground"
                  >
                    {t("system.keys_empty")}
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={cn("transition-colors", getRowCls(row.original))}
                    data-testid={`key-row-${row.original.key_id}`}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-3 py-2 whitespace-nowrap">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      <RecordRotationDialog
        open={recordTarget !== null}
        keyEntry={recordTarget}
        onClose={() => setRecordTarget(null)}
        onRecorded={() => qc.invalidateQueries({ queryKey: ["system", "keys"] })}
      />
    </div>
  );
}
