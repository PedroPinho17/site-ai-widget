// Markers become a single space so "Amendoeiras [1]48" stays "Amendoeiras 48"
// instead of gluing into "Amendoeiras48". Sources are shown as chips instead.
const CITE = /[ \t]*(?:【\d+】|\[\d+\])[ \t]*/g;

/** Strip citation marks; sources are shown as chips. */
export function stripCitations(text: string): string {
  return text
    .replace(CITE, " ")
    .replace(/[ \t]+([.,;:!?])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
}
