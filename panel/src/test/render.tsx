import { QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AppRoutes } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { I18nProvider } from "@/i18n/I18nProvider";
import { LocaleRoot } from "@/i18n/LocaleRoot";
import { setI18nLocale } from "@/i18n/state";
import { createQueryClient } from "@/lib/query-client";
import { loadSession } from "@/lib/session";

/** Tests run in English by default; `{ userLocale: true }` uses the real
 *  per-user locale flow (LocaleRoot) instead. */
export function renderPanel(route = "/", opts: { userLocale?: boolean } = {}): RenderResult {
  loadSession();
  setI18nLocale("en");
  const client = createQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>
          {opts.userLocale ? (
            <LocaleRoot>
              <AppRoutes />
            </LocaleRoot>
          ) : (
            <I18nProvider locale="en">
              <AppRoutes />
            </I18nProvider>
          )}
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
