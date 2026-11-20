"use client";

import { useQuery } from "@tanstack/react-query";
import { CreatorCard } from "@/components/cards/creator-card";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/states/states";
import { Select, SelectItem } from "@/components/ui/primitives";
import { creatorsQuery } from "@/lib/api/queries";
import type { CreatorView } from "@/lib/api/types";
import { useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

const SORT_LABELS: Record<string, string> = {
  match_share: "Share of videos on your topics",
  subscribers: "Subscribers",
  uploads_90d: "Uploads in 90 days",
  median_outlier_ratio: "Median vs their normal",
  hit_rate: "Videos beating their normal",
};

const GROUPS: { id: CreatorView["group"]; title: string; rule: string }[] = [
  { id: "strong", title: "Mostly on your topics", rule: "Most of their recent videos match your keywords." },
  { id: "related", title: "Sometimes on your topics", rule: "Some of their recent videos match your keywords." },
];

export default function CreatorsPage() {
  const clientId = useClientId();
  const { windowDays } = useViewParams();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const sort = params.get("sort") ?? undefined;
  const creators = useQuery(creatorsQuery(clientId, windowDays, sort));
  const data = creators.data?.data;
  const items = data?.items ?? [];

  function setSort(next: string) {
    const query = new URLSearchParams(params.toString());
    query.set("sort", next);
    router.replace(`${pathname}?${query.toString()}`, { scroll: false });
  }

  return (
    <>
      <PageHeader title="Creators" subtitle="Creators whose recent videos match your keywords, grouped by how closely. Raw numbers only; we don't rank creators." controls="window" />
      <FreshnessLine envelope={creators.data} />
      {data ? (
        <div className="mb-5">
          <Select label="Sort within each group by" selectedKey={data.sort} onSelectionChange={(key) => key && setSort(String(key))} className="w-64">
            {data.available_sorts.map((id) => (
              <SelectItem key={id} id={id} textValue={SORT_LABELS[id] ?? id}>
                {SORT_LABELS[id] ?? id.replace(/_/g, " ")}
              </SelectItem>
            ))}
          </Select>
        </div>
      ) : null}
      {creators.isPending ? <LoadingBlock /> : null}
      {creators.error ? <ErrorState error={creators.error} onRetry={() => creators.refetch()} /> : null}
      <div className="flex flex-col gap-8">
        {GROUPS.map((group) => {
          const members = items.filter((item) => item.group === group.id);
          if (!members.length) return null;
          return (
            <section key={group.id} aria-labelledby={`group-${group.id}`} className="flex flex-col gap-3">
              <div>
                <h2 id={`group-${group.id}`} className="text-section text-foreground">
                  {group.title} <span className="text-muted-foreground">· {members.length}</span>
                </h2>
                <p className="text-caption-1-regular text-muted-foreground">{group.rule}</p>
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                {members.map((creator) => (
                  <CreatorCard key={creator.channel.channel_id} creator={creator} />
                ))}
              </div>
            </section>
          );
        })}
      </div>
      {creators.data && !items.length ? <EmptyState title="No creators yet" body="No creators matched your topics with enough recent videos." /> : null}
    </>
  );
}
