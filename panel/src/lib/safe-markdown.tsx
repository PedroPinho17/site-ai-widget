import type { ReactNode } from "react";

import { stripCitations } from "@/lib/citations";

function inlineNodes(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /\*\*(.+?)\*\*|__(.+?)__/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(text.slice(last, match.index));
    }
    nodes.push(<strong key={`${keyPrefix}-b-${i++}`}>{match[1] ?? match[2]}</strong>);
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    nodes.push(text.slice(last));
  }
  return nodes;
}

/** Safe React tree for bold, lists and line breaks. No HTML from the server. */
export function SafeMarkdown({ text }: { text: string }) {
  const clean = stripCitations(text);
  const lines = clean.split("\n");
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  let key = 0;

  const flushList = () => {
    if (!list.length) {
      return;
    }
    const items = list;
    list = [];
    blocks.push(
      <ul key={`ul-${key++}`} className="list-disc space-y-1 pl-5">
        {items.map((item, index) => (
          <li key={`li-${index}`}>{inlineNodes(item, `li-${index}`)}</li>
        ))}
      </ul>,
    );
  };

  for (const line of lines) {
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      list.push(bullet[1]);
      continue;
    }
    flushList();
    if (!line.trim()) {
      blocks.push(<div key={`sp-${key++}`} className="h-2" />);
      continue;
    }
    blocks.push(
      <p key={`p-${key++}`} className="whitespace-pre-wrap">
        {inlineNodes(line, `p-${key}`)}
      </p>,
    );
  }
  flushList();
  return <div className="space-y-1 text-sm">{blocks}</div>;
}
