import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

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

describe("database page", () => {
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
        const body = JSON.parse(String(init.body)) as { password?: string };
        expect(body.password).toBe("fictional-db-secret");
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
    // Never kept in browser storage, and never sent back by a GET.
    for (const store of [sessionStorage, localStorage]) {
      for (let i = 0; i < store.length; i += 1) {
        expect(store.getItem(store.key(i) ?? "")).not.toContain("fictional-db-secret");
      }
    }
    expect(localStorage.length).toBe(0);
    expect(document.cookie).toBe("");
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
      // products and orders were approved and hidden by the filter: still approved.
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
    // products starts approved (checked); stores pending (unchecked)
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
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company());
      }
      if (url.endsWith("/db-connection") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, connection);
      }
      if (url.endsWith("/db-connection/introspect") && init?.method === "POST") {
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
    const boxes = screen.getAllByRole("checkbox");
    const categoriesBox = boxes[boxes.length - 1];
    expect(categoriesBox).not.toBeChecked();
  });
});
