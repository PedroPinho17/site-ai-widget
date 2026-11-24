import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { messageForCode } from "@/lib/api";
import { checkUploadFile, MAX_UPLOAD_BYTES } from "@/lib/documents";
import { setSession } from "@/lib/session";
import { jsonResponse, renderPanel } from "@/test/render";

const admin = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "admin@mercearia-atlas.example",
  role: "admin",
  company_id: "22222222-2222-2222-2222-222222222222",
};

const doc = {
  id: "55555555-5555-5555-5555-555555555555",
  original_name: "Mercearia Atlas - Manual.pdf",
  size_bytes: 12000,
  page_count: 3,
  status: "ready",
  created_at: "2026-11-24T09:00:00Z",
  updated_at: "2026-11-24T09:01:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("documents helpers", () => {
  it("refuses non-pdf and oversized files without needing the server", () => {
    expect(checkUploadFile(new File(["x"], "notes.txt", { type: "text/plain" }))).toBe("not_pdf");
    const big = new File([new Uint8Array(MAX_UPLOAD_BYTES + 1)], "big.pdf", {
      type: "application/pdf",
    });
    expect(checkUploadFile(big)).toBe("upload_too_large");
    expect(messageForCode("not_pdf")).toBe("Only PDF files are accepted.");
    expect(messageForCode("upload_too_large")).toBe("That file is too large.");
  });
});

describe("documents page", () => {
  it("lists documents and refuses bad uploads without calling the server", async () => {
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
      if (url.endsWith("/documents")) {
        return jsonResponse(200, [doc]);
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    renderPanel("/documents");

    expect(await screen.findByText("Mercearia Atlas - Manual.pdf")).toBeInTheDocument();
    expect(screen.getByText(/PDF ·/)).toBeInTheDocument();
    expect(screen.getByText(/· ready ·/)).toBeInTheDocument();

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const bad = new File(["hello"], "notes.txt", { type: "text/plain" });
    // Bypass the accept filter so the page's own type check runs.
    fireEvent.change(input, { target: { files: [bad] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Only PDF files are accepted.");
    expect(
      fetchMock.mock.calls.some((call) => (call as unknown as [string, RequestInit?])[1]?.method === "POST"),
    ).toBe(false);
  });

  it("asks for confirmation before delete", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, {
          id: admin.company_id,
          name: "Mercearia Atlas",
          slug: "mercearia-atlas",
          default_locale: "pt",
        });
      }
      if (url.endsWith("/documents") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, [doc]);
      }
      if (init?.method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    const user = userEvent.setup();
    renderPanel("/documents");

    await screen.findByText("Mercearia Atlas - Manual.pdf");
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByText("Delete this document?")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === "DELETE")).toBe(false);

    const dialogDelete = screen
      .getAllByRole("button", { name: "Delete" })
      .find((button) => button.closest('[role="dialog"]'));
    expect(dialogDelete).toBeTruthy();
    await user.click(dialogDelete!);
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => call[1]?.method === "DELETE")).toBe(true);
    });
  });

  it("polls while a document is processing, then stops", async () => {
    let listCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/company")) {
        return jsonResponse(200, { id: admin.company_id, name: "Mercearia Atlas", slug: "x", default_locale: "pt" });
      }
      if (url.endsWith("/documents")) {
        listCalls += 1;
        return jsonResponse(200, [{ ...doc, status: listCalls === 1 ? "processing" : "ready" }]);
      }
      return jsonResponse(404, { detail: { code: "not_found" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    setSession("test-jwt-token", admin);
    renderPanel("/documents");

    expect(await screen.findByText(/· processing ·/)).toBeInTheDocument();
    expect(await screen.findByText(/· ready ·/, undefined, { timeout: 5000 })).toBeInTheDocument();
    const settled = listCalls;
    await new Promise((resolve) => setTimeout(resolve, 3500));
    expect(listCalls).toBe(settled);
  }, 15000);
});

describe("dashboard", () => {
  it("shows a dash, not zero, when a count fails to load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/company")) {
          return jsonResponse(200, { id: admin.company_id, name: "Mercearia Atlas", slug: "x", default_locale: "pt" });
        }
        if (url.endsWith("/documents")) {
          return jsonResponse(500, { detail: { code: "internal", message: "raw stack trace" } });
        }
        if (url.endsWith("/widget-keys")) {
          return jsonResponse(200, []);
        }
        if (url.endsWith("/db-connection/schema")) {
          return jsonResponse(200, { tables: [] });
        }
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );
    setSession("test-jwt-token", admin);
    renderPanel("/");
    const docsCard = (await screen.findByText("Documents", { selector: "h3, h2, div, p, span" })).parentElement!;
    await waitFor(() => {
      expect(docsCard).toHaveTextContent("—");
    });
    expect(screen.queryByText("raw stack trace")).not.toBeInTheDocument();
  });
});

