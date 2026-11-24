import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildDbConnectionPayload } from "@/lib/db-connection";
import { setSession } from "@/lib/session";
import { jsonResponse, renderPanel } from "@/test/render";

const admin = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "admin@mercearia-atlas.example",
  role: "admin",
  company_id: "22222222-2222-2222-2222-222222222222",
};

const connection = {
  host: "127.0.0.1",
  port: 5436,
  database: "atlas",
  username: "atlas_readonly",
  status: "ok",
  last_error: null,
  last_checked_at: "2026-11-24T09:00:00Z",
  last_introspected_at: "2026-11-24T09:00:00Z",
  sslmode: "prefer" as const,
};

const products = {
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  schema_name: "public",
  name: "products",
  kind: "table",
  status: "approved",
  ai_suggested: false,
  description: null,
  is_present: true,
  columns: [
    {
      id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
      name: "id",
      data_type: "uuid",
      is_nullable: false,
      ordinal: 1,
      status: "approved",
      is_sensitive: false,
      description: null,
      is_present: true,
    },
  ],
};

const stores = {
  id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  schema_name: "public",
  name: "stores",
  kind: "table",
  status: "pending",
  ai_suggested: false,
  description: null,
  is_present: true,
  columns: [
    {
      id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
      name: "id",
      data_type: "uuid",
      is_nullable: false,
      ordinal: 1,
      status: "pending",
      is_sensitive: false,
      description: null,
      is_present: true,
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function company() {
  return {
    id: admin.company_id,
    name: "Mercearia Atlas",
    slug: "mercearia-atlas",
    default_locale: "pt",
  };
}

describe("db connection payload", () => {
  it("matches the API schema types and omits empty password on re-save", () => {
    const first = buildDbConnectionPayload(
      {
        host: "127.0.0.1",
        port: "5436",
        database: "atlas",
        username: "atlas_readonly",
        password: "fictional-db-secret",
        sslmode: "prefer",
      },
      null,
    );
    expect(first.ok).toBe(true);
    if (!first.ok) {
      throw new Error("expected payload");
    }
    expect(first.body).toEqual({
      host: "127.0.0.1",
      port: 5436,
      database: "atlas",
      username: "atlas_readonly",
      password: "fictional-db-secret",
      sslmode: "prefer",
    });
    expect(typeof first.body.port).toBe("number");

    const resave = buildDbConnectionPayload(
      {
        host: "127.0.0.1",
        port: "5436",
        database: "atlas",
        username: "atlas_readonly",
        password: "",
        sslmode: "require",
      },
      connection,
    );
    expect(resave).toEqual({
      ok: true,
      body: {
        host: "127.0.0.1",
        port: 5436,
        database: "atlas",
        username: "atlas_readonly",
        sslmode: "require",
      },
    });
    if (resave.ok) {
      expect("password" in resave.body).toBe(false);
    }
  });

  it("needs the password for a first save, a new target or weaker TLS", () => {
    const form = {
      host: "127.0.0.1",
      port: "5436",
      database: "atlas",
      username: "atlas_readonly",
      password: "",
      sslmode: "prefer" as const,
    };
    expect(buildDbConnectionPayload(form, null)).toEqual({ ok: false, code: "db_password_required" });
    for (const change of [
      { host: "db.attacker.example" },
      { port: "5437" },
      { database: "other" },
      { username: "someone_else" },
      { sslmode: "disable" as const },
    ]) {
      expect(buildDbConnectionPayload({ ...form, ...change }, connection)).toEqual({
        ok: false,
        code: "db_password_required",
      });
    }
    expect(buildDbConnectionPayload({ ...form, sslmode: "verify-full" }, connection).ok).toBe(true);
    expect(buildDbConnectionPayload({ ...form, host: "127.0.0.1", port: " " }, connection)).toEqual({
      ok: false,
      code: "db_host_invalid",
    });
  });
});

describe("database page", () => {
  it("treats a missing connection as empty, not an error", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection")) {
        return jsonResponse(404, { detail: { code: "not_found" } });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    renderPanel("/database");

    expect(await screen.findByText("No connection saved yet.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Not found.")).not.toBeInTheDocument();
  });

  it("never shows or keeps the password after save", async () => {
    let saved = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
        if (!saved) {
          return jsonResponse(404, { detail: { code: "not_found" } });
        }
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        expect(body.password).toBe("fictional-db-secret");
        expect(typeof body.port).toBe("number");
        expect(body.sslmode).toBe("prefer");
        saved = true;
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection/schema")) {
        return jsonResponse(200, { tables: [products, stores] });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/database");

    await user.type(screen.getByLabelText("Host"), "127.0.0.1");
    await user.clear(screen.getByLabelText("Port"));
    await user.type(screen.getByLabelText("Port"), "5436");
    await user.type(screen.getByLabelText("Database"), "atlas");
    await user.type(screen.getByLabelText("User"), "atlas_readonly");
    await user.type(screen.getByLabelText("Password"), "fictional-db-secret");
    await user.click(screen.getByRole("button", { name: "Save connection" }));

    await waitFor(() => {
      expect(screen.getByText("Connection saved.")).toBeInTheDocument();
    });
    expect(screen.queryByDisplayValue("fictional-db-secret")).not.toBeInTheDocument();
    expect(screen.queryByText("fictional-db-secret")).not.toBeInTheDocument();
    expect(screen.getByText("127.0.0.1")).toBeInTheDocument();
    expect(screen.getByText("atlas_readonly")).toBeInTheDocument();
    expect(screen.getByText(/Connected/)).toBeInTheDocument();
    for (const store of [sessionStorage, localStorage]) {
      for (let i = 0; i < store.length; i += 1) {
        expect(store.getItem(store.key(i) ?? "")).not.toContain("fictional-db-secret");
      }
    }
    expect(localStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("re-saves without a password and omits it from the payload", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        expect(body).toEqual({
          host: "127.0.0.1",
          port: 5436,
          database: "atlas",
          username: "atlas_readonly",
          sslmode: "prefer",
        });
        expect("password" in body).toBe(false);
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection/schema")) {
        return jsonResponse(200, { tables: [products, stores] });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/database");

    await screen.findByDisplayValue("127.0.0.1");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => {
      expect(screen.getByText("Connection saved.")).toBeInTheDocument();
    });
  });

  it("sends exactly one introspect request per Reload click and does not retry 429", async () => {
    let introspectCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection/schema")) {
        return jsonResponse(200, { tables: [products, stores] });
      }
      if (url.endsWith("/db-connection/introspect") && init?.method === "POST") {
        introspectCalls += 1;
        return jsonResponse(429, { detail: { code: "rate_limited" } });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/database");

    await screen.findByText(/public\.products/);
    await user.click(screen.getByRole("button", { name: "Reload schema" }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Too many requests");
    });
    expect(introspectCalls).toBe(1);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(introspectCalls).toBe(1);
  });

  it("a fast double click on Reload schema still sends one request", async () => {
    let introspectCalls = 0;
    let release: (() => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection/schema")) {
        return jsonResponse(200, { tables: [products, stores] });
      }
      if (url.endsWith("/db-connection/introspect") && init?.method === "POST") {
        introspectCalls += 1;
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return jsonResponse(200, { tables: 2, columns: 2, removed_tables: 0, needs_review: 0 });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    renderPanel("/database");

    await screen.findByText(/public\.products/);
    const button = screen.getByRole("button", { name: "Reload schema" });
    // Both clicks land before React re-renders the button as disabled.
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(introspectCalls).toBe(1));
    release?.();
    expect(await screen.findByText("Schema reloaded.")).toBeInTheDocument();
    expect(introspectCalls).toBe(1);
  });

  it("does not retry a failing connection load", async () => {
    let getCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/company")) {
          return jsonResponse(200, company());
        }
        if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
          getCalls += 1;
          return jsonResponse(429, { detail: { code: "rate_limited", message: "raw limiter text" } });
        }
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );
    setSession("test-jwt-token", admin);
    renderPanel("/database");
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests");
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(getCalls).toBe(1);
    expect(screen.queryByText("raw limiter text")).not.toBeInTheDocument();
  });

  it("keeps the saved SSL mode on re-save and refuses a new host without a password", async () => {
    const puts: Record<string, unknown>[] = [];
    const saved = { ...connection, sslmode: "require" as const };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, saved);
      }
      if (url.endsWith("/db-connection") && init?.method === "PUT") {
        puts.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return jsonResponse(200, saved);
      }
      if (url.endsWith("/db-connection/schema")) {
        return jsonResponse(200, { tables: [products] });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/database");

    await screen.findByDisplayValue("127.0.0.1");
    expect(screen.getByLabelText("SSL mode")).toHaveValue("require");
    await user.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].sslmode).toBe("require");
    expect("password" in puts[0]).toBe(false);

    const host = screen.getByLabelText("Host");
    await user.clear(host);
    await user.type(host, "db.attacker.example");
    await user.click(screen.getByRole("button", { name: "Save connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter the database password to change the connection.",
    );
    expect(puts).toHaveLength(1);
  });

  it("select all / none with a filter only changes the shown tables", async () => {
    const orders = { ...stores, id: "ffffffff-ffff-ffff-ffff-ffffffffffff", name: "orders", status: "approved" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection/schema") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, { tables: [products, stores, orders] });
      }
      if (url.endsWith("/db-connection/schema/approval") && init?.method === "PUT") {
        return jsonResponse(200, { tables: [products, stores, orders] });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/database");

    await screen.findByText(/public\.orders/);
    await user.type(screen.getByLabelText("Filter tables"), "stores");
    await user.click(screen.getByRole("button", { name: "Select shown" }));
    await user.click(screen.getByRole("button", { name: "Save approval" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((entry) => String(entry[0]).endsWith("/schema/approval"));
      expect(new Set(JSON.parse(String(call?.[1]?.body)).approved_table_ids)).toEqual(
        new Set([products.id, stores.id, orders.id]),
      );
    });

    fetchMock.mockClear();
    await user.clear(screen.getByLabelText("Filter tables"));
    await user.type(screen.getByLabelText("Filter tables"), "orders");
    await user.click(screen.getByRole("button", { name: "Clear shown" }));
    await user.click(screen.getByRole("button", { name: "Save approval" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((entry) => String(entry[0]).endsWith("/schema/approval"));
      expect(new Set(JSON.parse(String(call?.[1]?.body)).approved_table_ids)).toEqual(
        new Set([products.id, stores.id]),
      );
    });
  });

  it("saves approval with the selected tables only", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection/schema") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, { tables: [products, stores] });
      }
      if (url.endsWith("/db-connection/schema/approval") && init?.method === "PUT") {
        return jsonResponse(200, {
          tables: [
            { ...products, status: "approved" },
            { ...stores, status: "approved" },
          ],
        });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/database");

    expect(await screen.findByText(/public\.products/)).toBeInTheDocument();
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes[0]).toBeChecked();
    expect(boxes[1]).not.toBeChecked();
    await user.click(boxes[1]);
    await user.click(screen.getByRole("button", { name: "Save approval" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((entry) => String(entry[0]).endsWith("/schema/approval"));
      expect(call).toBeTruthy();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        approved_table_ids: [products.id, stores.id],
      });
    });
  });

  it("marks newly reloaded tables as not approved", async () => {
    const newcomers = {
      id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
      schema_name: "public",
      name: "categories",
      kind: "table",
      status: "pending",
      ai_suggested: false,
      description: null,
      is_present: true,
      columns: [],
    };
    let schemaCalls = 0;
    let introspectCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection/introspect") && init?.method === "POST") {
        introspectCalls += 1;
        return jsonResponse(200, { tables: 3, columns: 10, removed_tables: 0, needs_review: 1 });
      }
      if (url.endsWith("/db-connection/schema")) {
        schemaCalls += 1;
        if (schemaCalls === 1) {
          return jsonResponse(200, { tables: [products, stores] });
        }
        return jsonResponse(200, { tables: [products, stores, newcomers] });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/database");

    await screen.findByText(/public\.products/);
    await user.click(screen.getByRole("button", { name: "Reload schema" }));
    expect(await screen.findByText(/New tables: public\.categories/)).toBeInTheDocument();
    expect(screen.getByText(/public\.categories \(0 columns\)/)).toBeInTheDocument();
    expect(introspectCalls).toBe(1);
    const boxes = screen.getAllByRole("checkbox");
    const categoriesBox = boxes[boxes.length - 1];
    expect(categoriesBox).not.toBeChecked();
  });
});
