import type { ContextSourceKind, ProfileSource } from "@/lib/api/types";
import { SOURCE_KIND_LABELS } from "@/lib/labels";

export interface SourceGroup {
  kind: ContextSourceKind;
  pages: ProfileSource[];
  facts: number;
  blocked: boolean;
}

const SITE_TIERS: ReadonlySet<ContextSourceKind> = new Set(["site_direct", "site_rendered"]);
const KIND_ORDER = Object.keys(SOURCE_KIND_LABELS) as ContextSourceKind[];

export function isBlockedPage(source: ProfileSource): boolean {
  return source.claims === 0 && SITE_TIERS.has(source.source_kind);
}

// one chip per acquisition tier; pages inside keep the order the api ranked them in
export function groupSources(sources: ProfileSource[]): SourceGroup[] {
  const byKind = new Map<ContextSourceKind, ProfileSource[]>();
  for (const source of sources) byKind.set(source.source_kind, [...(byKind.get(source.source_kind) ?? []), source]);
  return KIND_ORDER.filter((kind) => byKind.has(kind)).map((kind) => {
    const pages = byKind.get(kind) ?? [];
    const facts = pages.reduce((sum, page) => sum + page.claims, 0);
    return { kind, pages, facts, blocked: pages.every(isBlockedPage) };
  });
}

export function pageLabel(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname.replace(/\/$/, "");
    return `${parsed.hostname.replace(/^www\./, "")}${path}${parsed.search}`;
  } catch {
    return url;
  }
}
