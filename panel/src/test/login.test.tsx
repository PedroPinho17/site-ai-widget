import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

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

describe("login", () => {
  it("stores the token and opens the dashboard", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/login")) {
        return jsonResponse(200, { access_token: "test-jwt-token", token_type: "bearer", user: userBody });
      }
      if (url.endsWith("/company")) {
        return jsonResponse(200, {
          id: userBody.company_id,
          name: "Mercearia Atlas",
          slug: "mercearia-atlas",
          default_locale: "pt",
        });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPanel("/login");
    await user.type(screen.getByLabelText("Email"), "admin@mercearia-atlas.example");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    });
    expect(sessionStorage.getItem("site-ai-panel:token")).toBe("test-jwt-token");
    expect(await screen.findByText("Mercearia Atlas")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalled();
    const loginCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/auth/login"));
    expect(loginCall?.[1]).toMatchObject({ credentials: "omit" });
  });

  it("shows a safe error and stays on login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(401, { detail: { code: "bad_login", message: "internal raw text" } })),
    );
    const user = userEvent.setup();
    renderPanel("/login");
    await user.type(screen.getByLabelText("Email"), "admin@mercearia-atlas.example");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.");
    expect(screen.queryByText("internal raw text")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("site-ai-panel:token")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});
