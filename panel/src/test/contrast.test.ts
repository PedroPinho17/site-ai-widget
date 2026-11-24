import { describe, expect, it } from "vitest";

import { COLOR_PRESETS, contrastRatio, onColor, parseHex } from "@/lib/contrast";
import { sourceChips } from "@/lib/api";

describe("contrast", () => {
  it("picks white or dark text that meets WCAG AA on each preset", () => {
    expect(COLOR_PRESETS).toHaveLength(8);
    for (const preset of COLOR_PRESETS) {
      const ink = onColor(preset);
      expect(contrastRatio(ink, preset)).toBeGreaterThanOrEqual(4.5);
    }
    expect(onColor("#ffffff")).toBe("#14221f");
    expect(onColor("#000000")).toBe("#ffffff");
    expect(parseHex("not-a-color")).toBeNull();
  });

  it("meets WCAG AA for every colour, mid-tones included", () => {
    expect(onColor("#777777")).toBe("#000000");
    let worst = Infinity;
    for (let r = 0; r < 256; r += 17) {
      for (let g = 0; g < 256; g += 17) {
        for (let b = 0; b < 256; b += 17) {
          const hex = `#${[r, g, b].map((n) => n.toString(16).padStart(2, "0")).join("")}`;
          worst = Math.min(worst, contrastRatio(onColor(hex), hex));
        }
      }
    }
    expect(worst).toBeGreaterThanOrEqual(4.5);
  });
});

describe("source chips", () => {
  it("hides chips when the assistant does not know", () => {
    expect(
      sourceChips({
        mode: "none",
        answer: "Não tenho essa informação nos documentos.",
        sources: [{ document_id: "d1", document_name: "Manual.pdf", page: 1 }],
      }),
    ).toEqual([]);
    expect(
      sourceChips({
        mode: "docs",
        answer: "Abrimos às 10h.",
        sources: [{ document_id: "d1", document_name: "Manual.pdf", page: 2 }],
      }),
    ).toEqual([
      { label: "Documento: Manual.pdf p. 2", documentId: "d1", page: 2 },
    ]);
  });
});
