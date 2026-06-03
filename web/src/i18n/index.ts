// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import ru from "./ru.json";

export const SUPPORTED_LOCALES = ["ru", "en"] as const;
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      ru: { translation: ru },
      en: { translation: en },
    },
    // RU is the primary language; EN is the fallback for untranslated tech messages
    lng: import.meta.env.VITE_APP_LOCALE ?? "ru",
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LOCALES,
    // Detector order: localStorage → navigator.language; cap to RU/EN
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
      lookupLocalStorage: "lotsman-locale",
    },
    interpolation: {
      escapeValue: false, // React already escapes
    },
  });

export default i18n;
