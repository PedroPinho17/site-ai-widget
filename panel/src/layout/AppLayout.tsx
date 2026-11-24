import { useQuery } from "@tanstack/react-query";
import { Menu, MessageCircle } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/i18n/useI18n";
import type { MessageKey } from "@/i18n/messages";
import { companyRequest, updatePanelLocaleRequest } from "@/lib/api";
import { getToken, setSession } from "@/lib/session";
import { cn } from "@/lib/utils";

const NAV: { to: string; label: MessageKey; end?: boolean; icon?: boolean }[] = [
  { to: "/", label: "nav.dashboard", end: true },
  { to: "/documents", label: "nav.documents" },
  { to: "/database", label: "nav.database" },
  { to: "/conversations", label: "nav.conversations" },
  { to: "/test", label: "nav.test", icon: true },
  { to: "/widget", label: "nav.widget" },
  { to: "/keys", label: "nav.keys" },
];

function NavItems({ onGo, t }: { onGo?: () => void; t: (key: MessageKey) => string }) {
  return (
    <nav className="flex flex-col gap-1 p-3">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={onGo}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium",
              isActive ? "bg-primary text-primary-foreground" : "text-foreground hover:bg-muted",
            )
          }
        >
          {item.icon ? <MessageCircle className="h-4 w-4" aria-hidden="true" /> : null}
          {t(item.label)}
        </NavLink>
      ))}
    </nav>
  );
}

export function AppLayout() {
  const { logout, user } = useAuth();
  const { t, locale, setLocale } = useI18n();
  const [open, setOpen] = useState(false);
  const company = useQuery({ queryKey: ["company"], queryFn: companyRequest });
  const name = company.data?.name ?? t("nav.company");

  async function switchLocale(next: "pt" | "en") {
    setLocale(next);
    if (user && getToken()) {
      try {
        const updated = await updatePanelLocaleRequest(next);
        setSession(getToken()!, { ...user, panel_locale: updated.panel_locale as "pt" | "en" });
      } catch {
        /* Locale still changes locally. */
      }
    }
  }

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[16rem_1fr]">
      <aside className="hidden border-r border-border bg-card lg:block">
        <p className="px-5 py-4 text-sm font-semibold text-primary">site-ai-widget</p>
        <NavItems t={t} />
      </aside>
      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            aria-label={t("nav.closeMenu")}
            onClick={() => setOpen(false)}
          />
          <aside className="relative z-10 h-full w-64 bg-card shadow-lg">
            <p className="px-5 py-4 text-sm font-semibold text-primary">site-ai-widget</p>
            <NavItems t={t} onGo={() => setOpen(false)} />
          </aside>
        </div>
      ) : null}
      <div className="flex min-h-screen flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-border bg-card px-4 py-3">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="lg:hidden"
              aria-label={t("nav.menu")}
              onClick={() => setOpen(true)}
            >
              <Menu className="h-5 w-5" />
            </Button>
            <p className="font-semibold">{name}</p>
          </div>
          <div className="flex items-center gap-2">
            <div role="group" aria-label={t("nav.language")} className="flex items-center gap-1">
              <Button
                type="button"
                variant={locale === "pt" ? "default" : "outline"}
                size="sm"
                aria-pressed={locale === "pt"}
                lang="pt-PT"
                onClick={() => void switchLocale("pt")}
              >
                {t("lang.pt")}
              </Button>
              <Button
                type="button"
                variant={locale === "en" ? "default" : "outline"}
                size="sm"
                aria-pressed={locale === "en"}
                lang="en"
                onClick={() => void switchLocale("en")}
              >
                {t("lang.en")}
              </Button>
            </div>
            <Button type="button" variant="outline" size="sm" onClick={logout}>
              {t("nav.logout")}
            </Button>
          </div>
        </header>
        <main className="flex-1 p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
