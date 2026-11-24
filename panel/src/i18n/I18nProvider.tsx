import { useCallback, useMemo, type ReactNode } from "react";

import { translate, type Locale, type MessageKey } from "@/i18n/messages";
import { setI18nLocale } from "@/i18n/state";
import { I18nContext } from "@/i18n/useI18n";

export function I18nProvider({
  children,
  locale,
  onLocaleChange,
}: {
  children: ReactNode;
  locale: Locale;
  onLocaleChange?: (locale: Locale) => void;
}) {
  // Non-React code (API error messages) reads the same locale. Set during
  // render so the first request after a switch already uses it.
  setI18nLocale(locale);

  const t = useCallback(
    (key: MessageKey, vars?: Record<string, string | number>) => translate(locale, key, vars),
    [locale],
  );

  const setLocale = useCallback(
    (next: Locale) => {
      onLocaleChange?.(next);
    },
    [onLocaleChange],
  );

  const value = useMemo(() => ({ locale, t, setLocale }), [locale, t, setLocale]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
