import { API_URL } from "@/lib/config";
import { errorMessage } from "@/i18n/messages";
import { getI18nLocale } from "@/i18n/state";
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

function localized(code: string): string | null {
  return errorMessage(getI18nLocale(), code);
}

function networkMessage(): string {
  return localized("network_error") ?? "Could not reach the server. Try again.";
}

/** Fixed, translated message for a status / code (never raw server text). */
function safeMessage(status: number, code: string): string {
  const known = localized(code);
  if (known) {
    return known;
  }
  if (status === 401) {
    return localized("session_invalid")!;
  }
  if (status === 403) {
    return localized("forbidden")!;
  }
  if (status === 429) {
    return localized("rate_limited")!;
  }
  if (status >= 500) {
    return localized("server_unavailable")!;
  }
  return localized("generic")!;
}

/** Fixed message for a known API / client check code (never raw server text). */
export function messageForCode(code: string, fallback?: string): string {
  return localized(code) ?? fallback ?? localized("generic")!;
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
    throw new ApiRequestError(0, "network_error", networkMessage());
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
  error?: string | null;
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
      panel_locale?: "pt" | "en";
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

export function uploadDocumentWithProgress(
  file: File,
  onProgress: (pct: number) => void,
): Promise<DocumentItem> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/documents`);
    xhr.setRequestHeader("Accept", "application/json");
    const token = getToken();
    if (token) {
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status === 401) {
        notifyUnauthorized();
        reject(new ApiRequestError(401, "session_invalid", safeMessage(401, "session_invalid")));
        return;
      }
      let data: unknown = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        data = null;
      }
      const code = readCode(data);
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new ApiRequestError(xhr.status, code || "error", safeMessage(xhr.status, code)));
        return;
      }
      resolve(data as DocumentItem);
    };
    xhr.onerror = () => {
      reject(new ApiRequestError(0, "network_error", networkMessage()));
    };
    const body = new FormData();
    body.append("file", file);
    xhr.send(body);
  });
}

export async function documentFileBlob(id: string): Promise<Blob> {
  const headers = new Headers({ Accept: "application/pdf" });
  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  let response: Response;
  try {
    response = await fetch(`${API_URL}/documents/${encodeURIComponent(id)}/file`, {
      headers,
      credentials: "omit",
    });
  } catch {
    throw new ApiRequestError(0, "network_error", networkMessage());
  }
  if (response.status === 401) {
    notifyUnauthorized();
    throw new ApiRequestError(401, "session_invalid", safeMessage(401, "session_invalid"));
  }
  if (!response.ok) {
    throw new ApiRequestError(response.status, "error", safeMessage(response.status, "error"));
  }
  // Pin the type: the blob: URL inherits the panel origin, so it must only
  // ever be rendered by the PDF viewer, never as HTML.
  const raw = await response.blob();
  return new Blob([raw], { type: "application/pdf" });
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
  show_suggestions: boolean;
  show_fullscreen: boolean;
  allow_visitor_documents: boolean;
  conversation_retention_days: number;
  suggested_questions: string[];
};

export function widgetSettingsRequest(): Promise<WidgetSettings> {
  return api("/company/widget-settings");
}

export function updateWidgetSettingsRequest(body: Partial<WidgetSettings>): Promise<WidgetSettings> {
  return api("/company/widget-settings", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export type ConversationListItem = {
  id: string;
  created_at: string | null;
  last_seen: string | null;
  lang: string | null;
  message_count: number;
  unanswered: boolean;
  answer_source: string;
};

export type ConversationDetail = {
  id: string;
  created_at: string | null;
  last_seen: string | null;
  lang: string | null;
  messages: {
    role: string;
    text: string | null;
    mode: string | null;
    unanswered: boolean;
    elapsed_ms: number | null;
    created_at: string | null;
  }[];
};

export function listConversationsRequest(params?: {
  unanswered?: boolean;
  date_from?: string;
  date_to?: string;
}): Promise<ConversationListItem[]> {
  const query = new URLSearchParams();
  if (params?.unanswered) {
    query.set("unanswered", "true");
  }
  if (params?.date_from) {
    query.set("date_from", params.date_from);
  }
  if (params?.date_to) {
    query.set("date_to", params.date_to);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return api(`/conversations${suffix}`);
}

export function getConversationRequest(id: string): Promise<ConversationDetail> {
  return api(`/conversations/${encodeURIComponent(id)}`);
}

export type DashboardStats = {
  questions_per_day: { day: string; count: number }[];
  source_share: { sql: number; docs: number; none: number; chat: number };
  top_unanswered: { question: string; count: number }[];
  average_response_ms: number | null;
};

export function dashboardStatsRequest(): Promise<DashboardStats> {
  return api("/stats/dashboard");
}

export function updatePanelLocaleRequest(panel_locale: "pt" | "en"): Promise<TokenResponse["user"]> {
  return api("/auth/locale", {
    method: "PATCH",
    body: JSON.stringify({ panel_locale }),
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
  lang?: "pt" | "en" | null;
};

/**
 * Answer language follows the question on the API. ``previous_lang`` is only
 * for short follow-ups (``ok``, ``Olá``); the panel UI locale is never sent.
 */
export function testAssistantRequest(
  question: string,
  previousLang?: "pt" | "en" | null,
  previousQuestion?: string | null,
): Promise<AssistantTestResponse> {
  const body: { question: string; previous_lang?: "pt" | "en"; previous_question?: string } = { question };
  if (previousLang === "pt" || previousLang === "en") {
    body.previous_lang = previousLang;
  }
  // Context for a short follow-up ("E o stock?"); the API ignores it for longer questions.
  if (previousQuestion) {
    body.previous_question = previousQuestion.slice(0, 1000);
  }
  return api("/assistant/test", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export type SourceChip = {
  label: string;
  documentId?: string;
  page?: number;
};

export function sourceChips(data: AssistantTestResponse): SourceChip[] {
  if (data.mode === "chat" || data.mode === "none") {
    return [];
  }
  const chips: SourceChip[] = [];
  for (const source of data.sources ?? []) {
    const name = source.document_name?.trim();
    if (!name) {
      continue;
    }
    chips.push({
      label: source.page ? `Documento: ${name} p. ${source.page}` : `Documento: ${name}`,
      documentId: source.document_id,
      page: source.page,
    });
  }
  for (const table of data.tables ?? []) {
    if (table.trim()) {
      chips.push({ label: `Base de dados: ${table.trim()}` });
    }
  }
  return chips;
}
