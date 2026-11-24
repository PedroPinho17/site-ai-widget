import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { messages } from "@/i18n/messages";
import { setI18nLocale } from "@/i18n/state";
import { documentFileBlob, messageForCode } from "@/lib/api";
import { MAX_FILES_PER_DROP } from "@/lib/documents";
import { getUser, setSession } from "@/lib/session";
import { buildPreviewHtml, escapeAttr, PREVIEW_SANDBOX } from "@/lib/widget-preview";
import { jsonResponse, renderPanel } from "@/test/render";

const admin = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "admin@mercearia-atlas.example",
  role: "admin",
  company_id: "22222222-2222-2222-2222-222222222222",
};

const settings = {
  company_id: admin.company_id,
  primary_color: "#0f766e",
  greeting: "Olá",
  locale: "pt",
  position: "bottom-right",
  assistant_name: "Atlas",
  currency: "EUR",
  show_suggestions: true,
  show_fullscreen: true,
  allow_visitor_documents: false,
  conversation_retention_days: 30,
  suggested_questions: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
  setI18nLocale("en");
});

function baseFetch(extra?: (url: string, init?: RequestInit) => Response | null) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const handled = extra?.(url, init);
    if (handled) {
      return handled;
    }
    if (url.endsWith("/company")) {
      return jsonResponse(200, { id: admin.company_id, name: "Mercearia Atlas", slug: "x", default_locale: "pt" });
    }
    if (url.endsWith("/company/widget-settings")) {
      return jsonResponse(200, settings);
    }
    if (url.endsWith("/documents") || url.endsWith("/widget-keys")) {
      return jsonResponse(200, []);
    }
    if (url.endsWith("/db-connection/schema")) {
      return jsonResponse(200, { tables: [] });
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
  });
}

describe("widget live preview", () => {
  it("escapes every attribute and cannot break out of the script tag", () => {
    const evil = `x' onload='alert(1)' "><img src=x onerror=alert(2)>`;
    const html = buildPreviewHtml({
      apiUrl: "http://127.0.0.1:8002",
      locale: "pt",
      primary_color: "red;background:url(x)",
      position: `bottom-left" onload="x`,
      assistant_name: evil,
      greeting: evil,
      questions: [evil],
      show_fullscreen: true,
      show_suggestions: true,
    });
    const doc = new DOMParser().parseFromString(html, "text/html");
    const scripts = doc.querySelectorAll("script");
    expect(scripts).toHaveLength(1);
    expect(doc.querySelectorAll("img")).toHaveLength(0);
    const script = scripts[0];
    const names = script.getAttributeNames();
    expect(names.some((name) => name.startsWith("on"))).toBe(false);
    expect(script.getAttribute("data-title")).toBe(evil);
    expect(JSON.parse(script.getAttribute("data-suggestions") ?? "[]")).toEqual([evil]);
    expect(script.getAttribute("data-color")).toBe("#2563eb");
    expect(script.getAttribute("data-position")).toBe("bottom-right");
    expect(escapeAttr(`'"<>&`)).toBe("&#39;&quot;&lt;&gt;&amp;");
  });

  it("renders in an opaque-origin sandbox with no token in the page", async () => {
    vi.stubGlobal("fetch", baseFetch());
    setSession("test-jwt-token-secret", admin);
    renderPanel("/widget");
    const frame = (await screen.findByTitle("Live preview")) as HTMLIFrameElement;
    const sandbox = frame.getAttribute("sandbox") ?? "";
    expect(sandbox).toBe(PREVIEW_SANDBOX);
    expect(sandbox).not.toContain("allow-same-origin");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    const srcdoc = frame.getAttribute("srcdoc") ?? "";
    expect(srcdoc).not.toContain("test-jwt-token-secret");
    expect(srcdoc).not.toContain("Bearer");
  });

  it("refuses a suggested question over 80 characters before calling the API", async () => {
    const fetchMock = baseFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/widget");
    const area = await screen.findByLabelText(/Suggested questions/);
    await user.type(area, "a".repeat(81));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 80 characters");
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
  });
});

describe("documents", () => {
  it(`refuses a drop of more than ${MAX_FILES_PER_DROP} files without uploading`, async () => {
    vi.stubGlobal("fetch", baseFetch());
    const xhrOpen = vi.fn();
    vi.stubGlobal(
      "XMLHttpRequest",
      class {
        upload = {};
        open = xhrOpen;
        setRequestHeader() {}
        send() {}
      },
    );
    setSession("test-jwt-token", admin);
    renderPanel("/documents");
    const zone = (await screen.findByText("Drag PDFs here, or choose files.")).parentElement!;
    const files = Array.from(
      { length: MAX_FILES_PER_DROP + 1 },
      (_, i) => new File(["%PDF-1.4"], `manual-${i}.pdf`, { type: "application/pdf" }),
    );
    fireEvent.drop(zone, { dataTransfer: { files } });
    expect(await screen.findByRole("alert")).toHaveTextContent(`Choose at most ${MAX_FILES_PER_DROP} files at a time.`);
    expect(xhrOpen).not.toHaveBeenCalled();
  });

  it("fetches the PDF with the JWT header and pins the blob type to PDF", async () => {
    const fetchMock = vi.fn(async () => new Response("<html><script>x</script></html>", { status: 200, headers: { "Content-Type": "text/html" } }));
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const blob = await documentFileBlob("55555555-5555-5555-5555-555555555555");
    expect(blob.type).toBe("application/pdf");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/documents/55555555-5555-5555-5555-555555555555/file");
    expect(url).not.toContain("token");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer test-jwt-token");
  });

  it("explains the per-company document limit", () => {
    expect(messageForCode("document_limit")).toMatch(/maximum number of documents/);
  });
});

describe("panel language", () => {
  it("uses the user's saved locale, translates every page and API errors, and saves a switch", async () => {
    const patches: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      baseFetch((url, init) => {
        if (url.endsWith("/auth/locale") && init?.method === "PATCH") {
          patches.push(JSON.parse(String(init.body)));
          return jsonResponse(200, { ...admin, panel_locale: "en" });
        }
        if (url.endsWith("/db-connection")) {
          return jsonResponse(404, { detail: { code: "not_found" } });
        }
        return null;
      }),
    );
    setSession("test-jwt-token", { ...admin, panel_locale: "pt" });
    const user = userEvent.setup();
    renderPanel("/keys", { userLocale: true });
    expect(await screen.findByRole("heading", { name: "Chaves e origens" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Criar chave" })).toBeInTheDocument();
    expect(messageForCode("forbidden")).toBe(messages.pt["error.forbidden"]);

    await user.click(screen.getByRole("link", { name: "Base de dados" }));
    expect(await screen.findByRole("heading", { name: "Base de dados" })).toBeInTheDocument();
    expect(screen.getByText("Ainda não há nenhuma ligação guardada.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "EN" }));
    expect(await screen.findByRole("heading", { name: "Database" })).toBeInTheDocument();
    await waitFor(() => expect(patches).toEqual([{ panel_locale: "en" }]));
    await waitFor(() => expect(getUser()?.panel_locale).toBe("en"));
    expect(messageForCode("forbidden")).toBe("You do not have permission to do that.");
  });
});

describe("widget colour picker", () => {
  it("keeps the colour input and hex field in sync and lists presets", async () => {
    vi.stubGlobal("fetch", baseFetch());
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/widget");
    const picker = await screen.findByLabelText("Primary colour");
    expect(picker).toHaveAttribute("type", "color");
    const hex = await screen.findByLabelText("Hex colour");
    await waitFor(() => expect(hex).toHaveValue("#0f766e"));
    fireEvent.change(hex, { target: { value: "#2563eb" } });
    expect(hex).toHaveValue("#2563eb");
    const preset = screen.getByRole("button", { name: "#be123c" });
    expect(preset).toHaveAttribute("aria-pressed", "false");
    await user.click(preset);
    expect(hex).toHaveValue("#be123c");
    expect(preset).toHaveAttribute("aria-pressed", "true");
    expect(picker).toHaveValue("#be123c");
  });

  it("flags an invalid hex and does not send it", async () => {
    const fetchMock = baseFetch();
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/widget");
    const hex = await screen.findByLabelText("Hex colour");
    await waitFor(() => expect(hex).toHaveValue("#0f766e"));
    fireEvent.change(hex, { target: { value: "#12zz" } });
    expect(hex).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Use a colour like #2563eb.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
  });
});
