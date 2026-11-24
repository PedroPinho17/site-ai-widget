const TOKEN_KEY = "site-ai-panel:token";
const USER_KEY = "site-ai-panel:user";

export type AuthUser = {
  id: string;
  email: string;
  role: string;
  company_id: string;
  panel_locale?: "pt" | "en";
};

let memoryToken: string | null = null;
let memoryUser: AuthUser | null = null;
const unauthorized = new Set<() => void>();

function readStoredUser(): AuthUser | null {
  try {
    const raw = sessionStorage.getItem(USER_KEY);
    if (!raw) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    const rec = parsed as Record<string, unknown>;
    if (
      typeof rec.id === "string" &&
      typeof rec.email === "string" &&
      typeof rec.role === "string" &&
      typeof rec.company_id === "string"
    ) {
      return {
        id: rec.id,
        email: rec.email,
        role: rec.role,
        company_id: rec.company_id,
        panel_locale: rec.panel_locale === "en" || rec.panel_locale === "pt" ? rec.panel_locale : undefined,
      };
    }
    return null;
  } catch {
    return null;
  }
}

export function loadSession(): { token: string | null; user: AuthUser | null } {
  try {
    memoryToken = sessionStorage.getItem(TOKEN_KEY);
  } catch {
    memoryToken = null;
  }
  memoryUser = memoryToken ? readStoredUser() : null;
  return { token: memoryToken, user: memoryUser };
}

export function getToken(): string | null {
  return memoryToken;
}

export function getUser(): AuthUser | null {
  return memoryUser;
}

export function setSession(token: string, user: AuthUser): void {
  memoryToken = token;
  memoryUser = user;
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    /* Private mode can block storage; in-memory session still works. */
  }
}

export function clearSession(): void {
  memoryToken = null;
  memoryUser = null;
  try {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

export function onUnauthorized(handler: () => void): () => void {
  unauthorized.add(handler);
  return () => {
    unauthorized.delete(handler);
  };
}

export function notifyUnauthorized(): void {
  clearSession();
  for (const handler of unauthorized) {
    handler();
  }
}
