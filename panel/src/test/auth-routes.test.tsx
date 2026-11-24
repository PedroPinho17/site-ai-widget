import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setSession } from "@/lib/session";
import { jsonResponse, renderPanel } from "@/test/render";

const userBody = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "admin@mercearia-atlas.example",
  role: "admin",
  company_id: "22222222-2222-2222-2222-222222222222",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("session guards", () => {
  it("redirects a protected route to login", async () => {
    renderPanel("/keys");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Keys & Origins" })).not.toBeInTheDocument();
  });

  it("logs out and returns to login on 401", async () => {
    setSession("stale-token", userBody);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/company") || url.endsWith("/widget-keys")) {
          return jsonResponse(401, { detail: { code: "session_invalid", message: "raw jwt dump" } });
        }
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );

    renderPanel("/keys");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    });
    expect(sessionStorage.getItem("site-ai-panel:token")).toBeNull();
    expect(screen.queryByText("raw jwt dump")).not.toBeInTheDocument();
  });
});
