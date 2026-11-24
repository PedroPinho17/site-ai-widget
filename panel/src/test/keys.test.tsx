import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { revokeKeyRequest, updateKeyOriginsRequest } from "@/lib/api";
import { setSession } from "@/lib/session";
import { jsonResponse, renderPanel } from "@/test/render";

const admin = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "admin@mercearia-atlas.example",
  role: "admin",
  company_id: "22222222-2222-2222-2222-222222222222",
};

const KEY_ID = "33333333-3333-3333-3333-333333333333";
const key = {
  id: KEY_ID,
  public_key: "wk_test_fictional_key_0001",
  allowed_origins: ["https://shop.example"],
  is_active: true,
  created_at: "2026-11-24T09:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function company(name: string) {
  return { id: admin.company_id, name, slug: "fictional", default_locale: "pt" };
}

describe("keys page", () => {
  it("validates origins like the API and never shows raw server text", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, company("Mercearia Atlas"));
      }
      if (url.endsWith("/widget-keys") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, [key]);
      }
      if (init?.method === "PATCH") {
        return jsonResponse(422, { detail: { code: "validation_error", message: "raw server detail" } });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/keys");

    const input = await screen.findByLabelText("Add origin");
    await user.type(input, "https://shop.example/path");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No path/);
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === "PATCH")).toBe(false);

    await user.clear(input);
    await user.type(input, "HTTPS://Loja.Example/");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("That value is not valid.");
    });
    const patch = fetchMock.mock.calls.find((call) => call[1]?.method === "PATCH");
    expect(String(patch?.[0])).toMatch(new RegExp(`/widget-keys/${KEY_ID}$`));
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
      allowed_origins: ["https://shop.example", "https://loja.example"],
    });
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
    expect((patch?.[1]?.headers as Headers).get("Authorization")).toBe("Bearer test-jwt-token");
    expect(localStorage.length).toBe(0);
    expect(document.cookie).toBe("");
  });

  it("does not show the previous company's cached data after a new login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/company")) {
          return jsonResponse(200, company("Mercearia Atlas"));
        }
        if (url.endsWith("/widget-keys")) {
          return jsonResponse(200, [key]);
        }
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );
    setSession("token-company-a", admin);
    const user = userEvent.setup();
    renderPanel("/keys");
    expect(await screen.findByText("Mercearia Atlas")).toBeInTheDocument();
    expect(await screen.findByText("https://shop.example")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Log out" }));
    await screen.findByRole("button", { name: "Sign in" });

    const other = { ...admin, company_id: "44444444-4444-4444-4444-444444444444" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith("/auth/login")) {
          return jsonResponse(200, { access_token: "token-company-b", token_type: "bearer", user: other });
        }
        // Company B's data never arrives, so anything on screen would be cached.
        return new Promise<Response>(() => {});
      }),
    );
    await user.type(screen.getByLabelText("Email"), "admin@padaria-lima.example");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.queryByText("Mercearia Atlas")).not.toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Keys & Origins" }));
    await screen.findByRole("heading", { name: "Keys & Origins" });
    expect(screen.queryByText("https://shop.example")).not.toBeInTheDocument();
  });
});

describe("api paths", () => {
  it("encodes key ids in the URL", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, key));
    vi.stubGlobal("fetch", fetchMock);
    await revokeKeyRequest("a/../b");
    await updateKeyOriginsRequest("a?x=1", []);
    const urls = fetchMock.mock.calls.map((call) => String((call as unknown[])[0]));
    expect(urls[0]).toMatch(/\/widget-keys\/a%2F\.\.%2Fb\/revoke$/);
    expect(urls[1]).toMatch(/\/widget-keys\/a%3Fx%3D1$/);
  });
});
