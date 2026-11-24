import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { messages } from "@/i18n/messages";
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

describe("panel i18n", () => {
  it("has matching keys in pt and en and European Portuguese wording", () => {
    expect(Object.keys(messages.pt).sort()).toEqual(Object.keys(messages.en).sort());
    expect(messages.pt["widget.fullscreen"]).toMatch(/ecrã inteiro/);
    expect(messages.pt["docs.title"]).toBe("Documentos");
    expect(messages.pt["login.password"]).toBe("Palavra-passe");
    for (const [key, text] of Object.entries(messages.pt)) {
      // Current European Portuguese (AO90) spelling, no Brazilian forms.
      expect(text, key).not.toMatch(/\b(activ|acç|acção|respectiv|seleccion|óptim|direcç|colecç)/i);
      expect(text, key).not.toMatch(/\b(senha|arquivo|tela|usuário|deletar|salvar|banco de dados)\b/i);
      // Every PT string is translated (only codes such as "PT"/"EN"/"ms"/"PDF" may match EN).
      if (!["lang.pt", "lang.en", "dashboard.ms", "docs.typePdf", "widget.title", "nav.widget", "login.email"].includes(key)) {
        expect(text, key).not.toBe(messages.en[key as keyof typeof messages.en]);
      }
    }
  });

  it("shows English copy in tests by default", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/company")) {
          return jsonResponse(200, {
            id: admin.company_id,
            name: "Mercearia Atlas",
            slug: "x",
            default_locale: "pt",
          });
        }
        if (url.endsWith("/documents") || url.endsWith("/widget-keys") || url.endsWith("/db-connection/schema")) {
          return jsonResponse(200, url.endsWith("/db-connection/schema") ? { tables: [] } : []);
        }
        if (url.endsWith("/stats/dashboard")) {
          return jsonResponse(200, {
            questions_per_day: [],
            source_share: { sql: 0, docs: 0, none: 0, chat: 0 },
            top_unanswered: [],
            average_response_ms: null,
          });
        }
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );
    setSession("test-jwt-token", admin);
    renderPanel("/");
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "PT" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "EN" })).toBeInTheDocument();
  });
});
