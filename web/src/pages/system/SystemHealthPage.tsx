// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * /system/health — Health dashboard.
 * Cards for each service; auto-refresh every 30 sec.
 * No TOTP required (read-only).
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { ru } from "date-fns/locale";
import { AlertCircle, CheckCircle2, Loader2, RefreshCw, XCircle } from "lucide-react";
import type * as React from "react";
import { useTranslation } from "react-i18next";
import { fetchSystemHealth } from "@/features/system/api";
import type { ServiceHealth, ServiceStatus } from "@/features/system/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";

// ── Status config ─────────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<
  ServiceStatus,
  { Icon: React.FC<React.SVGProps<SVGSVGElement>>; cls: string; label: string }
> = {
  ok: {
    Icon: CheckCircle2,
    cls: "text-status-ok bg-status-ok/10 border-status-ok/30",
    label: "OK",
  },
  degraded: {
    Icon: AlertCircle,
    cls: "text-status-soon bg-status-soon/10 border-status-soon/30",
    label: "Деградация",
  },
  down: {
    Icon: XCircle,
    cls: "text-status-overdue bg-status-overdue/10 border-status-overdue/30",
    label: "Недоступен",
  },
};

// ── Service card ──────────────────────────────────────────────────────────────

function ServiceCard({ service }: { service: ServiceHealth }) {
  const { t } = useTranslation();
  const config = STATUS_CONFIG[service.status];
  const { Icon, cls, label } = config;

  const lastSeenLabel = service.last_seen
    ? formatDistanceToNow(new Date(service.last_seen), { addSuffix: true, locale: ru })
    : t("system.health_never_seen");

  return (
    <div
      className={cn("rounded-xl border p-5 flex flex-col gap-3 shadow-sm", cls)}
      data-testid={`service-card-${service.name}`}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold text-sm">{service.name}</h3>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold border",
            cls,
          )}
        >
          <Icon className="h-3 w-3" aria-hidden />
          {label}
        </span>
      </div>

      <div className="flex flex-col gap-1 text-xs">
        <div className="flex justify-between">
          <span className="text-muted-foreground">{t("system.health_last_seen")}</span>
          <span className="font-mono">{lastSeenLabel}</span>
        </div>
        {service.uptime && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">{t("system.health_uptime")}</span>
            <span className="font-mono">{service.uptime}</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function HealthSkeleton() {
  return (
    <div className="animate-pulse rounded-xl border bg-muted/30 p-5 flex flex-col gap-3 h-28" />
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function SystemHealthPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const { data, isLoading, isError, isFetching } = useQuery({
    queryKey: ["system", "health"],
    queryFn: fetchSystemHealth,
    refetchInterval: 30_000,
    staleTime: 10_000,
  });

  const handleRefresh = () => {
    qc.invalidateQueries({ queryKey: ["system", "health"] });
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">{t("system.health_title")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t("system.health_subtitle")}</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleRefresh}
          disabled={isFetching}
          aria-busy={isFetching}
          className="gap-2"
        >
          <RefreshCw className={cn("h-4 w-4", isFetching && "animate-spin")} aria-hidden />
          {t("system.refresh")}
        </Button>
      </div>

      {isError && (
        <div
          role="alert"
          className="rounded bg-destructive/10 border border-destructive px-3 py-2 mb-6"
        >
          <p className="text-sm text-destructive">{t("system.health_error")}</p>
        </div>
      )}

      <section
        aria-busy={isLoading}
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
        aria-label={t("system.health_grid_label")}
      >
        {isLoading
          ? Array.from({ length: 8 }).map((_, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholders have no stable id
              <HealthSkeleton key={i} />
            ))
          : (data ?? []).map((service) => <ServiceCard key={service.name} service={service} />)}
      </section>

      {!isLoading && data && data.length === 0 && (
        <p className="text-center text-muted-foreground py-12">{t("system.health_empty")}</p>
      )}

      {/* Auto-refresh indicator */}
      <p className="mt-4 text-xs text-muted-foreground text-right" aria-live="polite">
        {isFetching ? (
          <span className="inline-flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            {t("system.refreshing")}
          </span>
        ) : (
          t("system.health_auto_refresh")
        )}
      </p>
    </div>
  );
}
