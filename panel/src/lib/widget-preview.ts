/** Live preview page for the Widget settings screen.
 *
 * The page is rendered in an iframe with `sandbox="allow-scripts"` (no
 * allow-same-origin), so it runs in an opaque origin and cannot reach the
 * panel's sessionStorage (JWT), cookies or DOM. It carries no token and no
 * widget key. Every value is attribute-escaped and double-quoted; enums and
 * the colour are re-checked here, not trusted from the form.
 */

export const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

export const PREVIEW_SANDBOX = "allow-scripts";

export type PreviewOptions = {
  apiUrl: string;
  locale: string;
  primary_color: string;
  position: string;
  assistant_name: string;
  greeting: string;
  questions: string[];
  show_fullscreen: boolean;
  show_suggestions: boolean;
};

export function escapeAttr(value: string): string {
  return value
    .split("&")
    .join("&amp;")
    .split('"')
    .join("&quot;")
    .split("'")
    .join("&#39;")
    .split("<")
    .join("&lt;")
    .split(">")
    .join("&gt;");
}

export function splitQuestions(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 6);
}

export function buildPreviewHtml(opts: PreviewOptions): string {
  const attrs = [
    `src="${escapeAttr(`${opts.apiUrl}/embed.js`)}"`,
    `data-preview="1"`,
    `data-open="1"`,
    `data-key="preview"`,
    `data-lang="${opts.locale === "en" ? "en" : "pt"}"`,
    `data-color="${HEX_COLOR.test(opts.primary_color) ? opts.primary_color : "#2563eb"}"`,
    `data-position="${opts.position === "bottom-left" ? "bottom-left" : "bottom-right"}"`,
    `data-title="${escapeAttr(opts.assistant_name)}"`,
    `data-greeting="${escapeAttr(opts.greeting)}"`,
    `data-suggestions="${escapeAttr(JSON.stringify(opts.questions))}"`,
    opts.show_fullscreen ? "" : `data-fullscreen="0"`,
    opts.show_suggestions ? "" : `data-suggestions-off="1"`,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer"></head>` +
    `<body style="margin:0;height:100vh;background:#f8fafc"><script ${attrs}></script></body></html>`
  );
}
