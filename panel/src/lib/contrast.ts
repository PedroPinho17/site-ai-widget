/** WCAG relative luminance and contrast against a hex background. */

export const COLOR_PRESETS = [
  "#0f766e",
  "#2563eb",
  "#7c3aed",
  "#be123c",
  "#b45309",
  "#334155",
  "#0e7490",
  "#c2410c",
] as const;

const HEX6 = /^#[0-9a-fA-F]{6}$/;

function channel(value: number): number {
  const n = value / 255;
  return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
}

export function parseHex(value: string): string | null {
  const text = value.trim();
  if (!HEX6.test(text)) {
    return null;
  }
  return text.toLowerCase();
}

export function relativeLuminance(hex: string): number {
  const parsed = parseHex(hex);
  if (!parsed) {
    return 0;
  }
  const r = channel(Number.parseInt(parsed.slice(1, 3), 16));
  const g = channel(Number.parseInt(parsed.slice(3, 5), 16));
  const b = channel(Number.parseInt(parsed.slice(5, 7), 16));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(foreground: string, background: string): number {
  const l1 = relativeLuminance(foreground);
  const l2 = relativeLuminance(background);
  const light = Math.max(l1, l2);
  const dark = Math.min(l1, l2);
  return (light + 0.05) / (dark + 0.05);
}

const WHITE = "#ffffff";
const DARK = "#14221f";
const BLACK = "#000000";
export const AA = 4.5;

export type OnColor = typeof WHITE | typeof DARK | typeof BLACK;

/** Text colour that meets WCAG AA (4.5:1) on ``background``: white, else the
 *  brand dark, else pure black. For any colour either white or black reaches
 *  at least 4.58:1, so the result always passes. Mirrors embed.js contrastOn. */
export function onColor(background: string): OnColor {
  const bg = parseHex(background) ?? "#2563eb";
  const white = contrastRatio(WHITE, bg);
  const dark = contrastRatio(DARK, bg);
  if (white >= AA && white >= dark) {
    return WHITE;
  }
  if (dark >= AA) {
    return DARK;
  }
  return white >= contrastRatio(BLACK, bg) ? WHITE : BLACK;
}
