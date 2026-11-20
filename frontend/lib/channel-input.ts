export type ParsedInput = { kind: "website"; url: string } | { kind: "channel"; ref: string } | { kind: "invalid" };

const CHANNEL_ID = /^UC[\w-]{22}$/;
const HANDLE = /^@[\w.-]{3,30}$/;

export function parseBusinessInput(raw: string): ParsedInput {
  const value = raw.trim();
  if (HANDLE.test(value) || CHANNEL_ID.test(value)) return { kind: "channel", ref: value };
  const withScheme = /^https?:\/\//i.test(value) ? value : `https://${value}`;
  try {
    const url = new URL(withScheme);
    if (!url.hostname.includes(".")) return { kind: "invalid" };
    const host = url.hostname.replace(/^www\.|^m\./, "");
    if (host === "youtube.com" || host === "youtu.be") return { kind: "channel", ref: url.toString() };
    return { kind: "website", url: url.toString() };
  } catch {
    return { kind: "invalid" };
  }
}

export function nameFromWebsite(url: string): string {
  const host = new URL(url).hostname.replace(/^www\./, "");
  const base = host.split(".")[0] ?? host;
  return base.charAt(0).toUpperCase() + base.slice(1);
}
