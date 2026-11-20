"use client";

import { useQuery } from "@tanstack/react-query";
import { CreatorCard } from "@/components/cards/creator-card";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/states/states";
import { CalcNote } from "@/components/ui/calc-mark";
import { creatorsQuery } from "@/lib/api/queries";
import type { CreatorView } from "@/lib/api/types";
import { useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";
import dynamic from "next/dynamic";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

// the select needs react-aria and shows only once creators have loaded
const CreatorSort = dynamic(() => import("@/components/cards/creator-sort"), { ssr: false, loading: () => <div aria-hidden className="h-[4.5rem]" /> });

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
          <CreatorSort sorts={data.available_sorts} selected={data.sort} onChange={setSort} />
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
              <CalcNote />
            </section>
          );
        })}
      </div>
      {creators.data && !items.length ? <EmptyState title="No creators yet" body="No creators matched your topics with enough recent videos." /> : null}
    </>
  );
}
