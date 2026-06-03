// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * /system/logs — Log viewer.
 * Service select + tail count input → fetch on demand.
 * Pre block with monospace, fixed height, scroll.
 * Auto-refresh toggle (off by default, 5 sec when on).
 * Copy-all button.
 */

import { useQuery } from "@tanstack/react-query";
import { ClipboardCopy, Loader2, RefreshCw } from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { fetchSystemLogs } from "@/features/system/api";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { toast } from "@/shared/ui/toast";

const SERVICES = [
  "auth-service",
  "registry-service",
  "notification-service",
  "audit-service",
  "web-bff",
  "system-control",
] as const;

type ServiceName = (typeof SERVICES)[number];

export function SystemLogsPage() {
  const { t } = useTranslation();
  const [selectedService, setSelectedService] = React.useState<ServiceName>("auth-service");
  const [tail, setTail] = React.useState(200);
  const [autoRefresh, setAutoRefresh] = React.useState(false);
  const [enabled, setEnabled] = React.useState(false);

  const { data, isFetching, isError, refetch } = useQuery({
    queryKey: ["system", "logs", selectedService, tail],
    queryFn: () => fetchSystemLogs({ service: selectedService, tail }),
    enabled,
    refetchInterval: autoRefresh ? 5_000 : false,
    staleTime: 0,
  });

  const handleLoad = () => {
    if (enabled) {
      refetch();
    } else {
      setEnabled(true);
    }
  };

  const handleCopy = async () => {
    if (!data?.lines.length) return;
    try {
      await navigator.clipboard.writeText(data.lines.join("\n"));
      toast.show({ title: t("system.logs_copied"), variant: "success" });
    } catch {
      toast.show({ title: t("system.logs_copy_failed"), variant: "destructive" });
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">{t("system.logs_title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("system.logs_subtitle")}</p>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-end gap-4 mb-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="logs-service">{t("system.logs_service_label")}</Label>
          <select
            id="logs-service"
            value={selectedService}
            onChange={(e) => {
              setSelectedService(e.target.value as ServiceName);
              setEnabled(false);
            }}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {SERVICES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="logs-tail">{t("system.logs_tail_label")}</Label>
          <Input
            id="logs-tail"
            type="number"
            min={10}
            max={500}
            value={tail}
            onChange={(e) => {
              const v = Math.min(500, Math.max(10, Number(e.target.value)));
              setTail(v);
              setEnabled(false);
            }}
            className="w-24"
          />
        </div>

        <Button onClick={handleLoad} disabled={isFetching} className="gap-2">
          {isFetching ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="h-4 w-4" aria-hidden />
          )}
          {t("system.logs_load_btn")}
        </Button>

        {/* Auto-refresh toggle */}
        <label className="flex items-center gap-2 cursor-pointer select-none text-sm">
          <button
            type="button"
            role="switch"
            aria-checked={autoRefresh}
            aria-label={t("system.logs_auto_refresh_label")}
            onClick={() => setAutoRefresh((v) => !v)}
            className={cn(
              "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent",
              "transition-colors duration-200 ease-in-out",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              autoRefresh ? "bg-primary" : "bg-muted",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0",
                "transition duration-200 ease-in-out",
                autoRefresh ? "translate-x-5" : "translate-x-0",
              )}
            />
          </button>
          {t("system.logs_auto_refresh_label")}
        </label>

        {data && (
          <Button variant="outline" size="sm" onClick={handleCopy} className="gap-2 ml-auto">
            <ClipboardCopy className="h-4 w-4" aria-hidden />
            {t("system.logs_copy_all")}
          </Button>
        )}
      </div>

      {isError && (
        <div
          role="alert"
          className="rounded bg-destructive/10 border border-destructive px-3 py-2 mb-4"
        >
          <p className="text-sm text-destructive">{t("system.logs_error")}</p>
        </div>
      )}

      {data?.truncated && (
        <div role="note" className="mb-2 text-xs text-muted-foreground" aria-live="polite">
          {t("system.logs_truncated")}
        </div>
      )}

      {/* Log output */}
      <div className="relative">
        <pre
          role="log"
          aria-live="polite"
          aria-busy={isFetching}
          className={cn(
            "rounded-md border border-border bg-muted/30 p-4",
            "font-mono text-xs leading-relaxed",
            "h-[600px] overflow-auto whitespace-pre-wrap break-all",
            !data && "flex items-center justify-center text-muted-foreground",
          )}
        >
          {!data && !isFetching && t("system.logs_placeholder")}
          {isFetching && (
            <span className="inline-flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {t("common.loading")}
            </span>
          )}
          {data &&
            !isFetching &&
            (data.lines.length === 0 ? t("system.logs_empty") : data.lines.join("\n"))}
        </pre>
      </div>
    </div>
  );
}
