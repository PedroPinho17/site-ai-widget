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
  not_pdf: "Only PDF files are accepted.",
  upload_too_large: "That file is too large.",
  pdf_encrypted: "Encrypted PDFs are not supported.",
  pdf_too_many_pages: "That PDF has too many pages.",
  pdf_corrupt: "Could not read that PDF.",
  document_duplicate: "That file was already uploaded.",
  connections_not_configured: "Database connections are not configured.",
  connection_unreadable: "The saved connection cannot be read. Save it again.",
  db_host_invalid: "Invalid database host or port.",
  db_host_unresolvable: "Could not resolve the database host.",
  db_host_not_allowed: "That host is not allowed.",
  db_unreachable: "Could not reach a PostgreSQL server at that host and port.",
  db_auth_failed: "Wrong username or password for the database.",
  db_not_found: "That database does not exist.",
  db_version_unsupported: "PostgreSQL 13 or newer is required.",
  db_user_can_write: "Use a read-only database user (SELECT only).",
  db_no_readable_tables: "This user cannot read any table.",
  db_connection_failed: "The database connection test failed.",
  db_schema_too_large: "This database has too many tables or columns.",
  db_timeout: "The database took too long to answer.",
  db_busy: "Too many database checks are running. Try again shortly.",
  db_schema_invalid: "The database sent invalid schema details.",
  db_password_required: "Enter the database password to change the connection.",
  db_conflict_retry: "Another change conflicted. Try again.",
  schema_not_present: "That table no longer exists in your database.",
  column_sensitive: "A sensitive column cannot be approved.",
  network_error: "Could not reach the server. Try again.",
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

/** Fixed message for a known API / client check code (never raw server text). */
export function messageForCode(code: string, fallback = "Something went wrong. Try again."): string {
  return SAFE_BY_CODE[code] ?? fallback;
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
  if (init.body != null && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
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
    throw new ApiRequestError(0, "network_error", SAFE_BY_CODE.network_error);
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
  if (response.status === 204) {
    return undefined as T;
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

export type DocumentItem = {
  id: string;
  original_name: string;
  size_bytes: number;
  page_count: number | null;
  status: string;
  created_at: string | null;
  updated_at: string | null;
};

export type DbConnection = {
  host: string;
  port: number;
  database: string;
  username: string;
  status: string;
  last_error: string | null;
  last_checked_at: string | null;
  last_introspected_at: string | null;
  readable_tables?: number | null;
  sslmode: SslMode;
};

export type SslMode = "disable" | "allow" | "prefer" | "require" | "verify-ca" | "verify-full";

export type DbConnectionSaveBody = {
  host: string;
  port: number;
  database: string;
  username: string;
  /** Omit on re-save to keep the stored password. */
  password?: string;
  sslmode: SslMode;
};

export type SchemaColumn = {
  id: string;
  name: string;
  data_type: string;
  is_nullable: boolean;
  ordinal: number;
  status: string;
  is_sensitive: boolean;
  description: string | null;
  is_present: boolean;
};

export type SchemaTable = {
  id: string;
  schema_name: string;
  name: string;
  kind: string;
  status: string;
  ai_suggested: boolean;
  description: string | null;
  is_present: boolean;
  columns: SchemaColumn[];
};

export type SchemaResponse = {
  tables: SchemaTable[];
};

export type IntrospectResponse = {
  tables: number;
  columns: number;
  removed_tables: number;
  needs_review: number;
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

export function listDocumentsRequest(): Promise<DocumentItem[]> {
  return api("/documents");
}

export function uploadDocumentRequest(file: File): Promise<DocumentItem> {
  const body = new FormData();
  body.append("file", file);
  return api("/documents", { method: "POST", body });
}

export function deleteDocumentRequest(id: string): Promise<void> {
  return api(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function getDbConnectionRequest(): Promise<DbConnection> {
  return api("/db-connection");
}

export function saveDbConnectionRequest(body: DbConnectionSaveBody): Promise<DbConnection> {
  return api("/db-connection", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function testDbConnectionRequest(): Promise<DbConnection> {
  return api("/db-connection/test", { method: "POST" });
}

export function introspectDbRequest(): Promise<IntrospectResponse> {
  return api("/db-connection/introspect", { method: "POST" });
}

export function getSchemaRequest(): Promise<SchemaResponse> {
  return api("/db-connection/schema");
}

export function saveSchemaApprovalRequest(approved_table_ids: string[]): Promise<SchemaResponse> {
  return api("/db-connection/schema/approval", {
    method: "PUT",
    body: JSON.stringify({ approved_table_ids }),
  });
}

export type WidgetSettings = {
  company_id: string;
  primary_color: string;
  greeting: string;
  locale: string;
  position: string;
  assistant_name: string;
  currency: string | null;
};

export function widgetSettingsRequest(): Promise<WidgetSettings> {
  return api("/company/widget-settings");
}

export function updateWidgetSettingsRequest(body: { currency: string | null }): Promise<WidgetSettings> {
  return api("/company/widget-settings", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export type AssistantCitation = {
  document_id: string;
  document_name: string;
  page: number;
};

export type AssistantTestResponse = {
  mode: "sql" | "docs" | "none" | "chat";
  answer?: string | null;
  message?: string | null;
  sources?: AssistantCitation[] | null;
  tables?: string[] | null;
  elapsed_ms?: number | null;
};

export function testAssistantRequest(question: string, lang: "pt" | "en"): Promise<AssistantTestResponse> {
  return api("/assistant/test", {
    method: "POST",
    body: JSON.stringify({ question, lang }),
  });
}

export function sourceChips(data: AssistantTestResponse): string[] {
  if (data.mode === "chat") {
    return [];
  }
  const chips: string[] = [];
  for (const source of data.sources ?? []) {
    const name = source.document_name?.trim();
    if (!name) {
      continue;
    }
    chips.push(source.page ? `Documento: ${name} p. ${source.page}` : `Documento: ${name}`);
  }
  for (const table of data.tables ?? []) {
    if (table.trim()) {
      chips.push(`Base de dados: ${table.trim()}`);
    }
  }
  return chips;
}
