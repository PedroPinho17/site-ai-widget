import { useCallback, useState, type ReactNode } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { I18nProvider } from "@/i18n/I18nProvider";
import type { Locale } from "@/i18n/messages";

/** Panel language: the signed-in user's saved choice (users.panel_locale),
 *  Portuguese before sign-in. */
export function LocaleRoot({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  // The saved per-user choice wins; a switch made in this tab applies only to
  // the user who made it (a different login starts from its own setting).
  const [override, setOverride] = useState<{ userId: string | null; locale: Locale } | null>(null);
  const userId = user?.id ?? null;
  const locale: Locale =
    override && override.userId === userId ? override.locale : (user?.panel_locale ?? "pt");
  const onLocaleChange = useCallback((next: Locale) => setOverride({ userId, locale: next }), [userId]);
  return (
    <I18nProvider locale={locale} onLocaleChange={onLocaleChange}>
      {children}
    </I18nProvider>
  );
}
