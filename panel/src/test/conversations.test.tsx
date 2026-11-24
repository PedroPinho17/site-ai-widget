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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("conversations page", () => {
  it("lists a conversation and opens it", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, {
          id: admin.company_id,
          name: "Mercearia Atlas",
          slug: "mercearia-atlas",
          default_locale: "pt",
        });
      }
      if (url.includes("/conversations/") && !url.endsWith("/conversations")) {
        return jsonResponse(200, {
          id: "sess-1",
          lang: "pt",
          messages: [{ role: "user", text: "Qual o horário?", mode: null, unanswered: false, elapsed_ms: null }],
        });
      }
      if (url.includes("/conversations")) {
        return jsonResponse(200, [
          {
            id: "sess-1",
            created_at: "2026-11-24T10:00:00Z",
            last_seen: "2026-11-24T10:01:00Z",
            lang: "pt",
            message_count: 2,
            unanswered: true,
            answer_source: "none",
          },
        ]);
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/conversations");
    // Codes are shown as words, never raw ("pt", "none"), and the time is formatted.
    expect(await screen.findByText(/Portuguese · 2 messages · Source: No answer · Unanswered/)).toBeInTheDocument();
    expect(screen.queryByText("2026-11-24T10:01:00Z")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open" }));
    await waitFor(() => {
      expect(screen.getByText("Qual o horário?")).toBeInTheDocument();
    });
    expect(screen.getByText(/^Visitor/)).toBeInTheDocument();
    const listCall = fetchMock.mock.calls.find(([input]) => String(input).includes("/conversations"));
    expect(String(listCall?.[0])).not.toContain("unanswered=");
  });

  it("refuses an inverted date range without calling the API", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, { id: admin.company_id, name: "Mercearia Atlas", slug: "x", default_locale: "pt" });
      }
      if (url.includes("/conversations")) {
        return jsonResponse(200, []);
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/conversations");
    await screen.findByText("No conversations yet.");
    await user.type(screen.getByLabelText("From"), "2026-11-20");
    await user.type(screen.getByLabelText("To"), "2026-11-10");
    expect(await screen.findByRole("alert")).toHaveTextContent("The start date must be on or before the end date.");
    const ranged = fetchMock.mock.calls.filter(([input]) => String(input).includes("date_to=2026-11-10"));
    expect(ranged).toHaveLength(0);
  });
});
