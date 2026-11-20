const VIDEO_ID = /^[\w-]{11}$/;

// accepts watch, youtu.be, shorts and live links or a bare 11-character id; anything else is null
export function videoIdFromUrl(input: string): string | null {
  const value = input.trim();
  if (VIDEO_ID.test(value)) return value;
  let url: URL;
  try {
    url = new URL(value.includes("://") ? value : `https://${value}`);
  } catch {
    return null;
  }
  const host = url.hostname.replace(/^(www|m)\./, "");
  const candidate =
    host === "youtu.be"
      ? url.pathname.slice(1)
      : host === "youtube.com"
        ? (url.searchParams.get("v") ?? url.pathname.match(/^\/(?:shorts|live|embed)\/([^/?]+)/)?.[1] ?? "")
        : "";
  return VIDEO_ID.test(candidate) ? candidate : null;
}
