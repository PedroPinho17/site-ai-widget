import { API_URL } from "@/lib/config";
import { getToken, notifyUnauthorized } from "@/lib/session";

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

const SAFE_BY_CODE: Record<string, string> = {
  bad_login: "Wrong email or password.",
  session_invalid: "Your session expired. Sign in again.",
  forbidden: "You do not have permission to do that.",
  validation_error: "That value is not valid.",
  not_found: "Not found.",
  rate_limited: "Too many requests. Try again shortly.",
  email_taken: "That email is already registered.",
  slug_taken: "That company slug is already taken.",
};

function safeMessage(status: number, code: string): string {
  if (SAFE_BY_CODE[code]) {
    return SAFE_BY_CODE[code];
  }
  if (status === 401) {
    return SAFE_BY_CODE.session_invalid;
  }
  if (status === 403) {
    return SAFE_BY_CODE.forbidden;
  }
  if (status === 429) {
    return SAFE_BY_CODE.rate_limited;
  }
  if (status >= 500) {
    return "The server is unavailable. Try again shortly.";
  }
  return "Something went wrong. Try again.";
}

function readCode(data: unknown): string {
  if (!data || typeof data !== "object") {
    return "";
  }
  const detail = (data as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") {
    return "";
  }
  const code = (detail as { code?: unknown }).code;
  return typeof code === "string" ? code : "";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (init.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers,
      credentials: "omit",
    });
  } catch {
    throw new ApiRequestError(0, "network_error", "Could not reach the server. Try again.");
  }

  const raw = await response.text();
  let data: unknown = null;
  if (raw) {
    try {
      data = JSON.parse(raw) as unknown;
    } catch {
      data = null;
    }
  }

  const code = readCode(data);
  if (response.status === 401 && path !== "/auth/login") {
    notifyUnauthorized();
    throw new ApiRequestError(401, code || "session_invalid", safeMessage(401, code || "session_invalid"));
  }
  if (!response.ok) {
    throw new ApiRequestError(response.status, code || "error", safeMessage(response.status, code));
  }
  return data as T;
}

export type Company = {
  id: string;
  name: string;
  slug: string;
  default_locale: string;
};

export type WidgetKey = {
  id: string;
  public_key: string;
  allowed_origins: string[];
  is_active: boolean;
  created_at: string;
};

export type TokenResponse = {
  access_token: string;
  token_type: string;
  user: {
    id: string;
    email: string;
    role: string;
    company_id: string;
  };
};

export function loginRequest(email: string, password: string): Promise<TokenResponse> {
  return api<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function meRequest(): Promise<TokenResponse["user"]> {
  return api("/auth/me");
}

export function companyRequest(): Promise<Company> {
  return api("/company");
}

export function listKeysRequest(): Promise<WidgetKey[]> {
  return api("/widget-keys");
}

export function createKeyRequest(): Promise<WidgetKey> {
  return api("/widget-keys", {
    method: "POST",
    body: JSON.stringify({ allowed_origins: [] }),
  });
}

export function revokeKeyRequest(id: string): Promise<WidgetKey> {
  return api(`/widget-keys/${encodeURIComponent(id)}/revoke`, { method: "POST" });
}

export function updateKeyOriginsRequest(id: string, allowed_origins: string[]): Promise<WidgetKey> {
  return api(`/widget-keys/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ allowed_origins }),
  });
}
