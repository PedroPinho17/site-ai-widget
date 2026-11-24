import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

const SSL_MODES: SslMode[] = ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"];

const EMPTY_FORM = {
  host: "",
  port: "5432",
  database: "",
  username: "",
  password: "",
  sslmode: "prefer" as SslMode,
};

function tableKey(table: SchemaTable): string {
  return `${table.schema_name}.${table.name}`;
}

function statusLabel(connection: DbConnection | undefined): string {
  if (!connection) {
    return "Not connected";
  }
  if (connection.status === "ok") {
    return "Connected";
  }
  if (connection.status === "failed") {
    return connection.last_error
      ? `Failed: ${messageForCode(connection.last_error, "The database connection test failed.")}`
      : "Failed";
  }
  return connection.status;
}

export function DatabasePage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const queryClient = useQueryClient();
  const [form, setForm] = useState(EMPTY_FORM);
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
      setNotice("Connection saved.");
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
      setNotice(data.status === "ok" ? "Connection test passed." : statusLabel(data));
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
      setNotice("Table approval saved.");
      await queryClient.invalidateQueries({ queryKey: ["db-schema"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : "Could not save approval.");
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
      setNotice("Schema reloaded.");
      queryClient.setQueryData(["db-schema"], result.after);
      await queryClient.invalidateQueries({ queryKey: ["db-connection"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : "Could not reload the schema.");
    },
  });

  function onSaveConnection(event: FormEvent) {
    event.preventDefault();
    const port = Number(form.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      setError(messageForCode("db_host_invalid"));
      return;
    }
    if (!form.password) {
      setError("Enter the database password to save the connection.");
      return;
    }
    saveConnection.mutate({
      host: form.host.trim(),
      port,
      database: form.database.trim(),
      username: form.username.trim(),
      password: form.password,
      sslmode: form.sslmode,
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

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Database</h1>
        <p className="text-sm text-muted-foreground">
          Read-only connection and which tables the widget may query.
        </p>
      </div>
      {notice ? <p className="text-sm text-primary">{notice}</p> : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      <Card>
        <CardTitle>Connection</CardTitle>
        {connection.isLoading ? (
          <p className="mt-2 text-sm text-muted-foreground">Loading connection…</p>
        ) : null}
        {connection.error instanceof ApiRequestError ? (
          <p role="alert" className="mt-2 text-sm text-destructive">
            {connection.error.message}
          </p>
        ) : null}
        {connection.data ? (
          <div className="mt-3 space-y-1 text-sm">
            <p>
              <span className="text-muted-foreground">Host:</span> {connection.data.host}
            </p>
            <p>
              <span className="text-muted-foreground">Port:</span> {connection.data.port}
            </p>
            <p>
              <span className="text-muted-foreground">Database:</span> {connection.data.database}
            </p>
            <p>
              <span className="text-muted-foreground">User:</span> {connection.data.username}
            </p>
            <p>
              <span className="text-muted-foreground">Status:</span> {statusLabel(connection.data)}
            </p>
          </div>
        ) : connection.data === null ? (
          <p className="mt-2 text-sm text-muted-foreground">No connection saved yet.</p>
        ) : null}

        {isAdmin ? (
          <form className="mt-4 grid gap-3 sm:grid-cols-2" onSubmit={onSaveConnection} autoComplete="off">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-host">Host</Label>
              <Input
                id="db-host"
                value={form.host}
                onChange={(event) => setForm((prev) => ({ ...prev, host: event.target.value }))}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-port">Port</Label>
              <Input
                id="db-port"
                value={form.port}
                onChange={(event) => setForm((prev) => ({ ...prev, port: event.target.value }))}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-name">Database</Label>
              <Input
                id="db-name"
                value={form.database}
                onChange={(event) => setForm((prev) => ({ ...prev, database: event.target.value }))}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-user">User</Label>
              <Input
                id="db-user"
                value={form.username}
                onChange={(event) => setForm((prev) => ({ ...prev, username: event.target.value }))}
                required
                autoComplete="off"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-password">Password</Label>
              <Input
                id="db-password"
                type="password"
                value={form.password}
                onChange={(event) => setForm((prev) => ({ ...prev, password: event.target.value }))}
                required
                autoComplete="new-password"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="db-sslmode">SSL mode</Label>
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
                {saveConnection.isPending ? "Saving…" : "Save connection"}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={!connection.data || testConnection.isPending}
                onClick={() => testConnection.mutate()}
              >
                {testConnection.isPending ? "Testing…" : "Test connection"}
              </Button>
            </div>
          </form>
        ) : null}
      </Card>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Schema approval</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              {approvedCount} of {presentTables.length} tables approved for the widget.
            </p>
          </div>
          {isAdmin ? (
            <Button
              type="button"
              variant="outline"
              disabled={!connection.data || reloadSchema.isPending}
              onClick={() => reloadSchema.mutate()}
            >
              {reloadSchema.isPending ? "Reloading…" : "Reload schema"}
            </Button>
          ) : null}
        </div>
        {reloadDiff ? (
          <div className="mt-3 space-y-1 text-sm">
            <p>
              New tables:{" "}
              {reloadDiff.added.length ? reloadDiff.added.join(", ") : "none"}
            </p>
            <p>
              Removed tables:{" "}
              {reloadDiff.removed.length ? reloadDiff.removed.join(", ") : "none"}
            </p>
          </div>
        ) : null}
        {schema.isLoading ? (
          <p className="mt-3 text-sm text-muted-foreground">Loading schema…</p>
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
                <Label htmlFor="schema-filter">Filter tables</Label>
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
                    {filter.trim() ? "Select shown" : "Select all"}
                  </Button>
                  <Button type="button" variant="outline" onClick={() => selectAll(false)}>
                    {filter.trim() ? "Clear shown" : "Select none"}
                  </Button>
                  <Button
                    type="button"
                    disabled={saveApproval.isPending}
                    onClick={() => saveApproval.mutate()}
                  >
                    {saveApproval.isPending ? "Saving…" : "Save approval"}
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
                        {tableKey(table)} ({table.columns.length} columns)
                      </span>
                    </label>
                    <span className="text-xs text-muted-foreground">
                      {canQuery ? "Widget can query" : "Not approved"}
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        ) : connection.data ? (
          <p className="mt-3 text-sm text-muted-foreground">
            No tables yet. Use Reload schema after saving a connection.
          </p>
        ) : null}
      </Card>
    </div>
  );
}
