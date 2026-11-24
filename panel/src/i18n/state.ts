import type { Locale } from "@/i18n/messages";

let current: Locale = "en";

export function getI18nLocale(): Locale {
  return current;
}

export function setI18nLocale(locale: Locale): void {
  current = locale === "pt" ? "pt" : "en";
}
