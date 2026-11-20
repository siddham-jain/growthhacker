import { RiForbidLine } from "@remixicon/react";
import { SourceKindChip } from "@/components/ui/chips";
import type { ProfileSource } from "@/lib/api/types";
import { formatDate } from "@/lib/format";

// the acquisition cascade made visible: blocked sources stay listed so the routing is never hidden
export function SourcesUsed({ sources }: { sources: ProfileSource[] }) {
  return (
    <section aria-labelledby="sources-used" className="flex flex-col gap-2">
      <h3 id="sources-used" className="text-eyebrow text-muted-foreground">
        Sources used
      </h3>
      <ul className="flex flex-wrap gap-2">
        {sources.map((source) => {
          const blocked = source.claims === 0 && (source.source_kind === "site_direct" || source.source_kind === "site_rendered");
          return (
            <li key={`${source.source_kind}-${source.url}`} className="flex items-center gap-1.5 rounded-full border border-border bg-card py-1 pr-3 pl-1 text-caption-1-regular text-muted-foreground" title={source.url}>
              <SourceKindChip kind={source.source_kind} className="rounded-full" />
              {blocked ? (
                <span className="inline-flex items-center gap-1 text-danger">
                  <RiForbidLine aria-hidden className="size-3.5" /> blocked, routed around
                </span>
              ) : (
                <span className="tabular-nums">
                  {source.claims} {source.claims === 1 ? "fact" : "facts"}
                  {source.snapshot_at ? ` · snapshot ${formatDate(source.snapshot_at)}` : ""}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
