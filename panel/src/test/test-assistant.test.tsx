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

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/company")) {
      return jsonResponse(200, {
        id: admin.company_id,
        name: "Mercearia Atlas",
        slug: "mercearia-atlas",
        default_locale: "pt",
      });
    }
    if (url.endsWith("/company/widget-settings") && init?.method === "PATCH") {
      return jsonResponse(200, {
        company_id: admin.company_id,
        primary_color: "#0f766e",
        greeting: "Olá",
        locale: "pt",
        position: "bottom-right",
        assistant_name: "Atlas",
        currency: "USD",
      });
    }
    if (url.endsWith("/company/widget-settings")) {
      return jsonResponse(200, {
        company_id: admin.company_id,
        primary_color: "#0f766e",
        greeting: "Olá",
        locale: "pt",
        position: "bottom-right",
        assistant_name: "Atlas",
        currency: "EUR",
      });
    }
    if (url.endsWith("/assistant/test")) {
      return jsonResponse(200, {
        mode: "sql",
        answer: "O azeite custa 8,49 €.",
        tables: ["products"],
        sources: null,
        elapsed_ms: 42,
      });
    }
    return jsonResponse(404, { detail: { code: "not_found" } });
  });
}

describe("test assistant page", () => {
  it("shows examples, a source chip, the time and clears the chat", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    renderPanel("/test");

    expect(await screen.findByRole("heading", { name: "Test assistant" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Qual o preço do azeite?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Qual o horário de abertura?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Olá" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Qual o preço do azeite?" }));
    expect(await screen.findByText("O azeite custa 8,49 €.")).toBeInTheDocument();
    expect(screen.getByText("Base de dados: products")).toBeInTheDocument();
    expect(screen.getByText("42 ms")).toBeInTheDocument();
    expect(screen.queryByText("A escrever…")).not.toBeInTheDocument();

    const posted = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/assistant/test"));
    expect(posted).toBeTruthy();
    // No lang from the panel: the API uses the company widget locale, so the
    // panel UI language and the settings loading state cannot change answers.
    expect(JSON.parse(String((posted?.[1] as RequestInit).body))).toEqual({
      question: "Qual o preço do azeite?",
    });

    await user.click(screen.getByRole("button", { name: "Clear chat" }));
    await waitFor(() => {
      expect(screen.queryByText("O azeite custa 8,49 €.")).not.toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Olá" })).toBeInTheDocument();
  });
});

describe("widget currency setting", () => {
  it("saves EUR from the currency select", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    renderPanel("/widget");

    const select = await screen.findByLabelText("Currency");
    await waitFor(() => {
      expect(select).toHaveValue("EUR");
    });
    await user.selectOptions(select, "USD");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      const saved = fetchMock.mock.calls.find((call) => {
        const init = call[1] as RequestInit | undefined;
        return String(call[0]).endsWith("/company/widget-settings") && init?.method === "PATCH";
      });
      expect(saved).toBeTruthy();
      expect(String((saved?.[1] as RequestInit).body)).toContain("USD");
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved ✓");
  });

  it("shows only fixed error text and renders duplicate chips without key clashes", async () => {
    const user = userEvent.setup();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/company")) {
          return jsonResponse(200, { id: admin.company_id, name: "Mercearia Atlas", slug: "x", default_locale: "pt" });
        }
        if (url.endsWith("/assistant/test")) {
          call += 1;
          if (call === 1) {
            return jsonResponse(500, {
              detail: { code: "internal", message: "relation \"secret_table\" does not exist" },
            });
          }
          return jsonResponse(200, {
            mode: "docs",
            answer: "Abrimos às 9h.",
            sources: [
              { document_id: "d1", document_name: "Manual.pdf", page: 2 },
              { document_id: "d1", document_name: "Manual.pdf", page: 2 },
            ],
            elapsed_ms: 7,
          });
        }
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );
    setSession("test-jwt-token", admin);
    renderPanel("/test");

    await user.click(await screen.findByRole("button", { name: "Olá" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The server is unavailable. Try again shortly.");
    expect(screen.queryByText(/secret_table/)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Question"), "Qual o horário?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Abrimos às 9h.")).toBeInTheDocument();
    expect(screen.getAllByText("Documento: Manual.pdf p. 2")).toHaveLength(2);
    const keyWarnings = consoleError.mock.calls.filter((args) => String(args[0]).includes("same key"));
    expect(keyWarnings).toHaveLength(0);
    consoleError.mockRestore();
  });

  it("on 429 shows the error, keeps the typed message and lets the admin retry", async () => {
    const user = userEvent.setup();
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/company")) {
          return jsonResponse(200, { id: admin.company_id, name: "Mercearia Atlas", slug: "x", default_locale: "pt" });
        }
        if (url.endsWith("/assistant/test")) {
          call += 1;
          if (call === 1) {
            return jsonResponse(429, { detail: { code: "rate_limited", message: "raw limiter detail" } });
          }
          return jsonResponse(200, { mode: "chat", answer: "Olá! Em que posso ajudar?", elapsed_ms: 3 });
        }
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );
    setSession("test-jwt-token", admin);
    renderPanel("/test");

    const box = await screen.findByLabelText("Question");
    await user.type(box, "Qual o horário de sábado?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests. Try again shortly.");
    expect(screen.queryByText("raw limiter detail")).not.toBeInTheDocument();
    expect(box).toHaveValue("Qual o horário de sábado?");
    // The unsent question is not left in the chat as if it had been asked.
    expect(screen.queryByText("Qual o horário de sábado?", { selector: "p" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Olá! Em que posso ajudar?")).toBeInTheDocument();
    expect(screen.getAllByText("Qual o horário de sábado?", { selector: "p" })).toHaveLength(1);
    expect(box).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("widget page shows an error and no success message when saving fails", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/company")) {
          return jsonResponse(200, { id: admin.company_id, name: "Mercearia Atlas", slug: "x", default_locale: "pt" });
        }
        if (url.endsWith("/company/widget-settings") && init?.method === "PATCH") {
          return jsonResponse(403, { detail: { code: "forbidden", message: "raw role detail" } });
        }
        if (url.endsWith("/company/widget-settings")) {
          return jsonResponse(200, {
            company_id: admin.company_id,
            primary_color: "#0f766e",
            greeting: "Olá",
            locale: "pt",
            position: "bottom-right",
            assistant_name: "Atlas",
            currency: "EUR",
          });
        }
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );
    setSession("test-jwt-token", { ...admin, role: "member" });
    renderPanel("/widget");

    const select = await screen.findByLabelText("Currency");
    await waitFor(() => expect(select).toHaveValue("EUR"));
    await user.selectOptions(select, "GBP");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have permission to do that.");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("raw role detail")).not.toBeInTheDocument();
  });
});