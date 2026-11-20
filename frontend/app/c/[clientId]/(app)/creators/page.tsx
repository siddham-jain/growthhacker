"use client";

import { useQuery } from "@tanstack/react-query";
import { CreatorCard } from "@/components/cards/creator-card";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, LoadingBlock } from "@/components/states/states";
import { creatorsQuery } from "@/lib/api/queries";
import { useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";

export default function CreatorsPage() {
  const clientId = useClientId();
  const { windowDays } = useViewParams();
  const creators = useQuery(creatorsQuery(clientId, windowDays));
  const items = creators.data?.data.items ?? [];
  return (
    <>
      <PageHeader title="Creators" subtitle="Creators whose recent videos match your topics, with how they perform against their own normal." controls="window" />
      <FreshnessLine envelope={creators.data} />
      <div className="flex flex-col gap-4">
        {creators.isPending ? <LoadingBlock /> : null}
        {items.map((creator) => (
          <CreatorCard key={creator.channel.channel_id} creator={creator} />
        ))}
        {creators.data && !items.length ? <EmptyState title="No creators yet" body="No creators matched your topics with enough recent videos." /> : null}
      </div>
    </>
  );
}
