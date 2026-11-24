import type { DbConnection, DbConnectionSaveBody, SslMode } from "@/lib/api";

export type ConnectionForm = {
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  sslmode: SslMode;
};

const SSL_STRENGTH: Record<SslMode, number> = {
  disable: 0,
  allow: 1,
  prefer: 2,
  require: 3,
  "verify-ca": 4,
  "verify-full": 5,
};

/**
 * Build the PUT body (same field types as the API's DbConnectionSave).
 * An empty password is omitted, which keeps the stored one, but only when the
 * target is unchanged and sslmode is not weaker; the API enforces the same rule.
 */
export function buildDbConnectionPayload(
  form: ConnectionForm,
  existing: DbConnection | null | undefined,
): { ok: true; body: DbConnectionSaveBody } | { ok: false; code: string } {
  const port = Number(form.port);
  if (!form.port.trim() || !Number.isInteger(port) || port < 1 || port > 65535) {
    return { ok: false, code: "db_host_invalid" };
  }
  const body: DbConnectionSaveBody = {
    host: form.host.trim(),
    port,
    database: form.database.trim(),
    username: form.username.trim(),
    sslmode: form.sslmode,
  };
  if (form.password) {
    body.password = form.password;
    return { ok: true, body };
  }
  const sameTarget =
    !!existing &&
    existing.host === body.host.toLowerCase() &&
    existing.port === body.port &&
    existing.database === body.database &&
    existing.username === body.username &&
    SSL_STRENGTH[body.sslmode] >= SSL_STRENGTH[existing.sslmode ?? "prefer"];
  if (!sameTarget) {
    return { ok: false, code: "db_password_required" };
  }
  return { ok: true, body };
}
