/** Escape a value for a double-quoted HTML attribute. */
function attr(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function embedSnippet(apiUrl: string, key: string, lang = "pt"): string {
  const base = apiUrl.replace(/\/+$/, "");
  return `<script src="${attr(`${base}/embed.js`)}" data-key="${attr(key)}" data-lang="${attr(lang)}" async></script>`;
}

export function keyPrefix(key: string): string {
  if (key.length <= 12) {
    return key;
  }
  return `${key.slice(0, 12)}…`;
}
