// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * /system/maintenance — Destructive operations.
 * 3 sections: Backup, Restart service, Apply migrations.
 * All use MaintenanceConfirmDialog (TOTP + typed literal confirmation).
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Database, RefreshCw, Server } from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
import {
  fetchSystemMigrations,
  migrateService,
  restartService,
  triggerBackupNow,
} from "@/features/system/api";
import { Button } from "@/shared/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { toast } from "@/shared/ui/toast";
import { MaintenanceConfirmDialog } from "./components/MaintenanceConfirmDialog";

type DialogMode = "backup" | "restart" | "migrate" | null;

const RESTARTABLE_SERVICES = [
  "auth-service",
  "registry-service",
  "notification-service",
  "audit-service",
  "web-bff",
] as const;

type RestartableService = (typeof RESTARTABLE_SERVICES)[number];

export function SystemMaintenancePage() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [dialogMode, setDialogMode] = React.useState<DialogMode>(null);
  const [restartService_name, setRestartServiceName] =
    React.useState<RestartableService>("auth-service");
  const [migrateService_name, setMigrateServiceName] = React.useState<string>("auth-service");

  const { data: migrations } = useQuery({
    queryKey: ["system", "migrations"],
    queryFn: fetchSystemMigrations,
    staleTime: 60_000,
  });

  const migrateableServices = React.useMemo(
    () => (migrations ?? []).map((m) => m.service),
    [migrations],
  );

  // Ensure migrateService_name is always a valid option
  React.useEffect(() => {
    if (migrateableServices.length > 0 && !migrateableServices.includes(migrateService_name)) {
      setMigrateServiceName(migrateableServices[0] ?? "auth-service");
    }
  }, [migrateableServices, migrateService_name]);

  const handleBackup = async (totpCode: string, _confirmation: string) => {
    const result = await triggerBackupNow({
      totp_code: totpCode,
      confirmation: "BACKUP NOW",
    });
    if (result.exit_code === 0) {
      toast.show({
        title: t("system.maintenance_backup_success"),
        description: `${result.duration_ms}ms`,
        variant: "success",
      });
    } else {
      toast.show({
        title: t("system.maintenance_backup_failed"),
        description: `exit_code=${result.exit_code}: ${result.stdout_tail.slice(0, 100)}`,
        variant: "destructive",
        duration: 8000,
      });
    }
    setDialogMode(null);
  };

  const handleRestart = async (totpCode: string, confirmation: string) => {
    await restartService({
      service: restartService_name,
      totp_code: totpCode,
      confirmation,
    });
    toast.show({
      title: t("system.maintenance_restart_success", { service: restartService_name }),
      variant: "success",
    });
    setDialogMode(null);
  };

  const handleMigrate = async (totpCode: string, confirmation: string) => {
    const result = await migrateService({
      service: migrateService_name,
      totp_code: totpCode,
      confirmation,
    });
    toast.show({
      title: t("system.maintenance_migrate_success", {
        service: migrateService_name,
        applied: result.applied,
      }),
      variant: "success",
    });
    setDialogMode(null);
    qc.invalidateQueries({ queryKey: ["system", "migrations"] });
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">{t("system.maintenance_title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("system.maintenance_subtitle")}</p>
      </div>

      {/* Global warning */}
      <div
        role="note"
        className="mb-8 flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3"
      >
        <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600 mt-0.5" aria-hidden />
        <p className="text-sm text-amber-900 font-medium">{t("system.maintenance_warning")}</p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Backup card */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <Database className="h-5 w-5 text-muted-foreground" aria-hidden />
              <CardTitle className="text-base">{t("system.maintenance_backup_title")}</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-xs text-muted-foreground">
              {t("system.maintenance_backup_description")}
            </p>
            <Button variant="outline" onClick={() => setDialogMode("backup")} className="gap-2">
              <Database className="h-4 w-4" aria-hidden />
              {t("system.maintenance_backup_btn")}
            </Button>
          </CardContent>
        </Card>

        {/* Restart service card */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <RefreshCw className="h-5 w-5 text-muted-foreground" aria-hidden />
              <CardTitle className="text-base">{t("system.maintenance_restart_title")}</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-xs text-muted-foreground">
              {t("system.maintenance_restart_description")}
            </p>
            <div className="flex flex-col gap-2">
              <select
                aria-label={t("system.maintenance_restart_select_label")}
                value={restartService_name}
                onChange={(e) => setRestartServiceName(e.target.value as RestartableService)}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {RESTARTABLE_SERVICES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <Button variant="outline" onClick={() => setDialogMode("restart")} className="gap-2">
                <RefreshCw className="h-4 w-4" aria-hidden />
                {t("system.maintenance_restart_btn")}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Apply migrations card */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <Server className="h-5 w-5 text-muted-foreground" aria-hidden />
              <CardTitle className="text-base">{t("system.maintenance_migrate_title")}</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-xs text-muted-foreground">
              {t("system.maintenance_migrate_description")}
            </p>
            <div className="flex flex-col gap-2">
              <select
                aria-label={t("system.maintenance_migrate_select_label")}
                value={migrateService_name}
                onChange={(e) => setMigrateServiceName(e.target.value)}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {migrateableServices.length > 0 ? (
                  migrateableServices.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))
                ) : (
                  <option value="auth-service">auth-service</option>
                )}
              </select>
              <Button variant="outline" onClick={() => setDialogMode("migrate")} className="gap-2">
                <Server className="h-4 w-4" aria-hidden />
                {t("system.maintenance_migrate_btn")}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Backup dialog */}
      <MaintenanceConfirmDialog
        open={dialogMode === "backup"}
        onClose={() => setDialogMode(null)}
        title={t("system.maintenance_backup_title")}
        description={t("system.maintenance_backup_confirm_description")}
        expected="BACKUP NOW"
        onConfirm={handleBackup}
      />

      {/* Restart dialog */}
      <MaintenanceConfirmDialog
        open={dialogMode === "restart"}
        onClose={() => setDialogMode(null)}
        title={t("system.maintenance_restart_title")}
        description={t("system.maintenance_restart_confirm_description", {
          service: restartService_name,
        })}
        expected={restartService_name}
        onConfirm={handleRestart}
      />

      {/* Migrate dialog */}
      <MaintenanceConfirmDialog
        open={dialogMode === "migrate"}
        onClose={() => setDialogMode(null)}
        title={t("system.maintenance_migrate_title")}
        description={t("system.maintenance_migrate_confirm_description", {
          service: migrateService_name,
        })}
        expected={migrateService_name}
        onConfirm={handleMigrate}
      />
    </div>
  );
}
