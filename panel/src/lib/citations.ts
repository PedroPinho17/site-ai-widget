// A marker and the spaces before it go together, so "9h 【1】 e aos sábados [2]."
// reads "9h e aos sábados.". Sources are shown as chips instead.
const CITE = /[ \t]*(?:【\d+】|\[\d+\])/g;

/** Strip citation marks; sources are shown as chips. */
export function stripCitations(text: string): string {
  return text.replace(CITE, "").replace(/[ \t]{2,}/g, " ").replace(/[ \t]+\n/g, "\n").trim();
}
