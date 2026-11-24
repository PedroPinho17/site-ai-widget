import { describe, expect, it } from "vitest";

import { embedSnippet, keyPrefix } from "@/lib/snippet";

describe("embedSnippet", () => {
  it("builds the one-line script tag", () => {
    expect(embedSnippet("http://127.0.0.1:8002/", "wk_demo_mercearia_atlas")).toBe(
      '<script src="http://127.0.0.1:8002/embed.js" data-key="wk_demo_mercearia_atlas" data-lang="pt" async></script>',
    );
  });

  it("shortens the public key for display", () => {
    expect(keyPrefix("wk_demo_mercearia_atlas")).toBe("wk_demo_merc…");
  });
});

describe("embedSnippet escaping", () => {
  it("escapes attribute values", () => {
    const snippet = embedSnippet("http://127.0.0.1:8002", 'wk_"><img src=x onerror=alert(1)>');
    expect(snippet).toContain('data-key="wk_&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"');
    expect(snippet).not.toContain("<img");
  });
});
