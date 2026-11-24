/**
 * Client-side mirror of the API's `normalize_origin` (src/site_ai_widget/api/schemas.py).
 * Both run the shared vectors in tests/fixtures/origin_cases.json, so the panel
 * accepts and canonicalises exactly what the server does. The server stays the
 * authority; this only gives early feedback.
 */
const DEFAULT_PORTS: Record<string, number> = { http: 80, https: 443 };
const MAX_ORIGIN_LEN = 300;
const DNS_LABEL = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;
const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

function validDnsName(host: string): boolean {
  if (host.length > 253) {
    return false;
  }
  const labels = host.split(".");
  if (!labels.every((label) => DNS_LABEL.test(label))) {
    return false;
  }
  // A numeric final label is never a real TLD ("999.1.1.1" is not a host).
  return !/^\d+$/.test(labels[labels.length - 1]);
}

function toAscii(host: string): string | null {
  if (/^[\x20-\x7e]*$/.test(host)) {
    return host.toLowerCase();
  }
  try {
    // IDN → punycode, like Python's "idna" codec.
    return new URL(`http://${host}`).hostname.toLowerCase();
  } catch {
    return null;
  }
}

function ipv4OrDnsHost(host: string): string | null {
  if (IPV4.test(host)) {
    return host;
  }
  const ascii = toAscii(host);
  return ascii && validDnsName(ascii) ? ascii : null;
}

function ipv6Host(inner: string): string | null {
  if (inner.includes("%")) {
    return null;
  }
  try {
    const host = new URL(`http://[${inner}]`).hostname;
    return host.startsWith("[") ? host : null;
  } catch {
    return null;
  }
}

/** Canonical `scheme://host[:port]`, or null when the API would refuse it. */
export function parseAllowedOrigin(raw: string): string | null {
  const text = raw.trim();
  if (!text || text.length > MAX_ORIGIN_LEN) {
    return null;
  }
  for (const ch of text) {
    if (/\s/.test(ch) || ch.charCodeAt(0) < 0x20 || ch === "\\" || ch === "*") {
      return null;
    }
  }
  if (text.includes("?") || text.includes("#")) {
    return null;
  }
  const match = /^([a-z][a-z0-9+.-]*):\/\/([^/]*)(.*)$/i.exec(text);
  if (!match) {
    return null;
  }
  const scheme = match[1].toLowerCase();
  const netloc = match[2];
  const path = match[3];
  if (!(scheme in DEFAULT_PORTS) || netloc.includes("@") || (path !== "" && path !== "/")) {
    return null;
  }

  let hostPart: string;
  let portText: string;
  let hostOut: string | null;
  if (netloc.startsWith("[")) {
    const close = netloc.indexOf("]");
    if (close < 0) {
      return null;
    }
    hostPart = netloc.slice(1, close);
    const after = netloc.slice(close + 1);
    if (after && !after.startsWith(":")) {
      return null;
    }
    portText = after.slice(1);
    hostOut = hostPart ? ipv6Host(hostPart) : null;
  } else {
    const colon = netloc.lastIndexOf(":");
    hostPart = colon >= 0 ? netloc.slice(0, colon) : netloc;
    portText = colon >= 0 ? netloc.slice(colon + 1) : "";
    hostOut = hostPart ? ipv4OrDnsHost(hostPart) : null;
  }
  if (!hostOut) {
    return null;
  }

  let port: number | null = null;
  if (portText) {
    if (!/^\d+$/.test(portText)) {
      return null;
    }
    port = Number(portText);
    if (port < 1 || port > 65535) {
      return null;
    }
  }
  if (port === null || port === DEFAULT_PORTS[scheme]) {
    return `${scheme}://${hostOut}`;
  }
  return `${scheme}://${hostOut}:${port}`;
}

export function originError(raw: string): string | null {
  if (!raw.trim()) {
    return "Enter an origin.";
  }
  if (parseAllowedOrigin(raw)) {
    return null;
  }
  return "Use http(s)://host with an optional port. No path, query, wildcard or user info.";
}
