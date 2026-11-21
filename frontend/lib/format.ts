const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

export function formatCompact(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : compact.format(value);
}

export function formatExact(value: number, locale = "en-IN"): string {
  return new Intl.NumberFormat(locale).format(value);
}

export function formatRatio(ratio: number | null | undefined): string {
  return ratio === null || ratio === undefined ? "no baseline yet" : `${ratio.toFixed(1)}×`;
}

// a median views ratio in words: how a channel's typical recent video compares with its own usual
export function describeVsUsual(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return "Too few videos yet to know their usual views";
  if (ratio >= 2) return `Typical video lately: ${ratio.toFixed(1)}× their usual views`;
  const percent = Math.round(Math.abs(ratio - 1) * 100);
  if (percent < 5) return "Typical video lately: about their usual views";
  return `Typical video lately: ${percent}% ${ratio > 1 ? "above" : "below"} their usual views`;
}

export function formatDate(iso: string, timeZone = "Asia/Kolkata"): string {
  const date = new Date(iso);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: sameYear ? undefined : "numeric", timeZone });
}

export function formatDateTime(iso: string, timeZone = "Asia/Kolkata"): string {
  return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone, timeZoneName: "short" });
}

export function formatRelative(iso: string, now = Date.now()): string {
  const minutes = Math.round((now - Date.parse(iso)) / 60_000);
  if (Math.abs(minutes) < 1) return "just now";
  const format = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (Math.abs(minutes) < 60) return format.format(-minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 48) return format.format(-hours, "hour");
  return format.format(-Math.round(hours / 24), "day");
}

export function formatCountdown(expiresAt: string, now = Date.now()): string {
  const hours = (Date.parse(expiresAt) - now) / 3_600_000;
  if (hours <= 0) return "closed";
  if (hours < 1) return "closes within the hour";
  if (hours < 48) return `closes in ${Math.round(hours)} h`;
  return `closes in ${Math.round(hours / 24)} days`;
}

export function formatElapsed(fromIso: string | null, now = Date.now()): string {
  if (!fromIso) return "0:00";
  const seconds = Math.max(0, Math.floor((now - Date.parse(fromIso)) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}
