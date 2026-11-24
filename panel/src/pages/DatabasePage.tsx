import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState, type FormEvent } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useI18n } from "@/i18n/useI18n";
import type { MessageKey } from "@/i18n/messages";
import {
  ApiRequestError,
  getDbConnectionRequest,
  getSchemaRequest,
  introspectDbRequest,
  messageForCode,
  saveDbConnectionRequest,
  saveSchemaApprovalRequest,
  testDbConnectionRequest,
  type DbConnection,
  type SchemaTable,
  type SslMode,
} from "@/lib/api";
import { buildDbConnectionPayload, type ConnectionForm } from "@/lib/db-connection";

const SSL_MODES: SslMode[] = ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"];

const EMPTY_FORM: ConnectionForm = {
  host: "",
  port: "5432",
  database: "",
  username: "",
  password: "",
  sslmode: "prefer",
};

function tableKey(table: SchemaTable): string {
  return `${table.schema_name}.${table.name}`;
}

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string;

function statusLabel(connection: DbConnection | undefined, t: Translate): string {
  if (!connection) {
    return t("db.notConnected");
  }
  if (connection.status === "ok") {
    return t("db.connected");
  }
  if (connection.status === "failed") {
    return connection.last_error
      ? t("db.failedWith", { reason: messageForCode(connection.last_error, messageForCode("db_connection_failed")) })
      : t("db.failed");
  }
  return connection.status;
}

function formFromConnection(conn: DbConnection): ConnectionForm {
  return {
    host: conn.host,
    port: String(conn.port),
    database: conn.database,
    username: conn.username,
    password: "", // never fill from the API
    sslmode: SSL_MODES.includes(conn.sslmode) ? conn.sslmode : "prefer",
  };
}

export function DatabasePage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ConnectionForm>(EMPTY_FORM);
  const [formSource, setFormSource] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectionSource, setSelectionSource] = useState("");
  const [reloadDiff, setReloadDiff] = useState<{ added: string[]; removed: string[] } | null>(null);

  const connection = useQuery({
    queryKey: ["db-connection"],
    queryFn: async () => {
      try {
        return await getDbConnectionRequest();
      } catch (err) {
        // First visit: no row yet. Treat as empty, not an error state.
        if (err instanceof ApiRequestError && err.status === 404) {
          return null;
        }
        throw err;
      }
    },
  });

  const schema = useQuery({
    queryKey: ["db-schema"],
    queryFn: getSchemaRequest,
    enabled: connection.data !== null && connection.data !== undefined,
  });

  const presentTables = useMemo(
    () => (schema.data?.tables ?? []).filter((table) => table.is_present),
    [schema.data],
  );

  const schemaSelectionKey = presentTables
    .map((table) => `${table.id}:${table.status}`)
    .join("|");
  if (schemaSelectionKey !== selectionSource) {
    setSelectionSource(schemaSelectionKey);
    setSelected(
      new Set(presentTables.filter((table) => table.status === "approved").map((table) => table.id)),
    );
  }

  // Prefill host/port/database/user when a connection loads; never the password.
  const connectionKey = connection.data
    ? `${connection.data.host}|${connection.data.port}|${connection.data.database}|${connection.data.username}|${connection.data.sslmode}`
    : "";
  if (connection.data && connectionKey !== formSource) {
    setFormSource(connectionKey);
    setForm(formFromConnection(connection.data));
  }

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) {
      return presentTables;
    }
    return presentTables.filter((table) => tableKey(table).toLowerCase().includes(q));
  }, [filter, presentTables]);

  const saveConnection = useMutation({
    mutationFn: saveDbConnectionRequest,
    onSuccess: async () => {
      setForm((prev) => ({ ...prev, password: "" }));
      setError("");
      setNotice(t("db.saved"));
      await queryClient.invalidateQueries({ queryKey: ["db-connection"] });
      await queryClient.invalidateQueries({ queryKey: ["db-schema"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : messageForCode("db_connection_failed"));
    },
  });

  const testConnection = useMutation({
    mutationFn: testDbConnectionRequest,
    onSuccess: async (data) => {
      setError("");
      setNotice(data.status === "ok" ? t("db.testPassed") : statusLabel(data, t));
      await queryClient.invalidateQueries({ queryKey: ["db-connection"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : messageForCode("db_connection_failed"));
    },
  });

  const saveApproval = useMutation({
    mutationFn: () => saveSchemaApprovalRequest([...selected]),
    onSuccess: async () => {
      setError("");
      setNotice(t("db.approvalSaved"));
      await queryClient.invalidateQueries({ queryKey: ["db-schema"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : t("db.approvalFail"));
    },
  });

  const reloadSchema = useMutation({
    mutationFn: async () => {
      const before = new Map(
        (schema.data?.tables ?? [])
          .filter((table) => table.is_present)
          .map((table) => [table.id, tableKey(table)]),
      );
      await introspectDbRequest();
      const after = await getSchemaRequest();
      const afterPresent = after.tables.filter((table) => table.is_present);
      const afterIds = new Set(afterPresent.map((table) => table.id));
      const added = afterPresent
        .filter((table) => !before.has(table.id))
        .map((table) => tableKey(table));
      const removed = [...before.entries()]
        .filter(([id]) => !afterIds.has(id))
        .map(([, name]) => name);
      return { after, added, removed };
    },
    onSuccess: async (result) => {
      setReloadDiff({ added: result.added, removed: result.removed });
      setError("");
      setNotice(t("db.reloaded"));
      queryClient.setQueryData(["db-schema"], result.after);
      await queryClient.invalidateQueries({ queryKey: ["db-connection"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : t("db.reloadFail"));
    },
  });

  function onSaveConnection(event: FormEvent) {
    event.preventDefault();
    const built = buildDbConnectionPayload(form, connection.data);
    if (!built.ok) {
      setError(messageForCode(built.code));
      return;
    }
    saveConnection.mutate(built.body);
  }

  // A ref, not isPending: two clicks in the same render must still send one request.
  const reloadInFlight = useRef(false);
  function onReloadSchema() {
    if (reloadInFlight.current) {
      return;
    }
    reloadInFlight.current = true;
    reloadSchema.mutate(undefined, {
      onSettled: () => {
        reloadInFlight.current = false;
      },
    });
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  // Only the tables the filter shows change. Hidden tables keep their
  // selection, so saving never un-approves a table the admin could not see.
  function selectAll(on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const table of filtered) {
        if (on) {
          next.add(table.id);
        } else {
          next.delete(table.id);
        }
      }
      return next;
    });
  }

  const approvedCount = presentTables.filter((table) => table.status === "approved").length;
  const hasConnection = Boolean(connection.data);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">{t("db.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("db.subtitle")}</p>
      </div>
      {notice ? <p className="text-sm text-primary">{notice}</p> : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      <Card>
        <CardTitle>{t("db.connection")}</CardTitle>
        {connection.isLoading ? (
          <p className="mt-2 text-sm text-muted-foreground">{t("db.loadingConnection")}</p>
        ) : null}
        {connection.isError && connection.error instanceof ApiRequestError ? (
          <p role="alert" className="mt-2 text-sm text-destructive">
            {connection.error.message}
          </p>
        ) : null}
        {connection.data ? (
          <div className="mt-3 space-y-1 text-sm">
            <p>
              <span className="text-muted-foreground">{t("db.host")}:</span> {connection.data.host}
            </p>
            <p>
              <span className="text-muted-foreground">{t("db.port")}:</span> {connection.data.port}
            </p>
            <p>
              <span className="text-muted-foreground">{t("db.database")}:</span> {connection.data.database}
            </p>
            <p>
              <span className="text-muted-foreground">{t("db.user")}:</span> {connection.data.username}
            </p>
            <p>
              <span className="text-muted-foreground">{t("db.status")}:</span> {statusLabel(connection.data, t)}
            </p>
          </div>
        ) : connection.data === null && !connection.isLoading ? (
          <p className="mt-2 text-sm text-muted-foreground">{t("db.noConnection")}</p>
        ) : null}

        {isAdmin ? (
          <form className="mt-4 grid gap-3 sm:grid-cols-2" onSubmit={onSaveConnection} autoComplete="off">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-host">{t("db.host")}</Label>
              <Input
                id="db-host"
                value={form.host}
                onChange={(event) => setForm((prev) => ({ ...prev, host: event.target.value }))}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-port">{t("db.port")}</Label>
              <Input
                id="db-port"
                value={form.port}
                onChange={(event) => setForm((prev) => ({ ...prev, port: event.target.value }))}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-name">{t("db.database")}</Label>
              <Input
                id="db-name"
                value={form.database}
                onChange={(event) => setForm((prev) => ({ ...prev, database: event.target.value }))}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-user">{t("db.user")}</Label>
              <Input
                id="db-user"
                value={form.username}
                onChange={(event) => setForm((prev) => ({ ...prev, username: event.target.value }))}
                required
                autoComplete="off"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-password">{t("db.password")}</Label>
              <Input
                id="db-password"
                type="password"
                value={form.password}
                onChange={(event) => setForm((prev) => ({ ...prev, password: event.target.value }))}
                required={!hasConnection}
                placeholder={hasConnection ? t("db.keepPassword") : undefined}
                autoComplete="new-password"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-sslmode">{t("db.sslmode")}</Label>
              <select
                id="db-sslmode"
                className="h-10 rounded-md border border-border bg-card px-3 text-sm"
                value={form.sslmode}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, sslmode: event.target.value as SslMode }))
                }
              >
                {SSL_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-wrap gap-2 sm:col-span-2">
              <Button type="submit" disabled={saveConnection.isPending}>
                {saveConnection.isPending ? t("db.saving") : t("db.saveConnection")}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={!hasConnection || testConnection.isPending}
                onClick={() => testConnection.mutate()}
              >
                {testConnection.isPending ? t("db.testing") : t("db.testConnection")}
              </Button>
            </div>
          </form>
        ) : null}
      </Card>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>{t("db.schemaApproval")}</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("db.approvedCount", { approved: approvedCount, total: presentTables.length })}
            </p>
          </div>
          {isAdmin ? (
            <Button
              type="button"
              variant="outline"
              disabled={!hasConnection || reloadSchema.isPending}
              onClick={onReloadSchema}
            >
              {reloadSchema.isPending ? t("db.reloading") : t("db.reload")}
            </Button>
          ) : null}
        </div>
        {reloadDiff ? (
          <div className="mt-3 space-y-1 text-sm">
            <p>
              {t("db.newTables")}{" "}
              {reloadDiff.added.length ? reloadDiff.added.join(", ") : t("db.noneLower")}
            </p>
            <p>
              {t("db.removedTables")}{" "}
              {reloadDiff.removed.length ? reloadDiff.removed.join(", ") : t("db.noneLower")}
            </p>
          </div>
        ) : null}
        {schema.isLoading ? (
          <p className="mt-3 text-sm text-muted-foreground">{t("db.loadingSchema")}</p>
        ) : null}
        {schema.error instanceof ApiRequestError ? (
          <p role="alert" className="mt-3 text-sm text-destructive">
            {schema.error.message}
          </p>
        ) : null}
        {connection.data && presentTables.length > 0 ? (
          <>
            <div className="mt-4 flex flex-wrap items-end gap-2">
              <div className="flex min-w-56 flex-1 flex-col gap-1.5">
                <Label htmlFor="schema-filter">{t("db.filter")}</Label>
                <Input
                  id="schema-filter"
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                  placeholder="public.products"
                />
              </div>
              {isAdmin ? (
                <>
                  <Button type="button" variant="outline" onClick={() => selectAll(true)}>
                    {filter.trim() ? t("db.selectShown") : t("db.selectAll")}
                  </Button>
                  <Button type="button" variant="outline" onClick={() => selectAll(false)}>
                    {filter.trim() ? t("db.clearShown") : t("db.selectNone")}
                  </Button>
                  <Button
                    type="button"
                    disabled={saveApproval.isPending}
                    onClick={() => saveApproval.mutate()}
                  >
                    {saveApproval.isPending ? t("db.saving") : t("db.saveApproval")}
                  </Button>
                </>
              ) : null}
            </div>
            <ul className="mt-4 flex flex-col gap-2">
              {filtered.map((table) => {
                const checked = selected.has(table.id);
                const canQuery = table.status === "approved";
                return (
                  <li
                    key={table.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted px-3 py-2"
                  >
                    <label className="flex items-center gap-2 text-sm">
                      {isAdmin ? (
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggle(table.id)}
                        />
                      ) : null}
                      <span>
                        {tableKey(table)} {t("db.columns", { n: table.columns.length })}
                      </span>
                    </label>
                    <span className="text-xs text-muted-foreground">
                      {canQuery ? t("db.canQuery") : t("db.notApproved")}
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        ) : connection.data ? (
          <p className="mt-3 text-sm text-muted-foreground">{t("db.noTables")}</p>
        ) : null}
      </Card>
    </div>
  );
}
