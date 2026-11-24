import { useQuery } from "@tanstack/react-query";
import { Menu, MessageCircle } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/button";
import { companyRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV: { to: string; label: string; end?: boolean; icon?: boolean }[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/documents", label: "Documents" },
  { to: "/database", label: "Database" },
  { to: "/conversations", label: "Conversations" },
  { to: "/test", label: "Test assistant", icon: true },
  { to: "/widget", label: "Widget" },
  { to: "/keys", label: "Keys & Origins" },
];

function NavItems({ onGo }: { onGo?: () => void }) {
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
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

export function AppLayout() {
  const { logout } = useAuth();
  const [open, setOpen] = useState(false);
  const company = useQuery({ queryKey: ["company"], queryFn: companyRequest });
  const name = company.data?.name ?? "Company";

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[16rem_1fr]">
      <aside className="hidden border-r border-border bg-card lg:block">
        <p className="px-5 py-4 text-sm font-semibold text-primary">site-ai-widget</p>
        <NavItems />
      </aside>
      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            aria-label="Close menu"
            onClick={() => setOpen(false)}
          />
          <aside className="relative z-10 h-full w-64 bg-card shadow-lg">
            <p className="px-5 py-4 text-sm font-semibold text-primary">site-ai-widget</p>
            <NavItems onGo={() => setOpen(false)} />
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
              aria-label="Open menu"
              onClick={() => setOpen(true)}
            >
              <Menu className="h-5 w-5" />
            </Button>
            <p className="font-semibold">{name}</p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={logout}>
            Log out
          </Button>
        </header>
        <main className="flex-1 p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
