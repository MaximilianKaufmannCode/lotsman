// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { useTranslation } from "react-i18next";

export function SettingsPage() {
  const { t } = useTranslation();

  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold">{t("settings.title")}</h1>
      <p className="mt-2 text-muted-foreground">Настройки системы — будет в следующих итерациях.</p>
    </div>
  );
}
