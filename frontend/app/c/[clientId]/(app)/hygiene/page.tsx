"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCard } from "@/components/cards/refresh-card";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, InfoNote, LoadingBlock } from "@/components/states/states";
import { refreshQuery } from "@/lib/api/queries";
import { useClientId } from "@/lib/use-client-id";

export default function HygienePage() {
  const clientId = useClientId();
  const refresh = useQuery(refreshQuery(clientId));
  const fixes = (refresh.data?.data.items ?? []).filter((item) => item.basis === "rule_based");
  return (
    <>
      <PageHeader title="Channel hygiene" subtitle="Small description fixes: missing calls to action, broken links. Each follows a fixed rule, so there are no reference videos." controls="none" />
      <div className="flex flex-col gap-4">
        <InfoNote>These are rule-based fixes, kept apart from recommendations because they don&apos;t rest on outlier evidence.</InfoNote>
        {refresh.isPending ? <LoadingBlock /> : null}
        {fixes.map((suggestion) => (
          <RefreshCard key={suggestion.id} suggestion={suggestion} />
        ))}
        {refresh.data && !fixes.length ? <EmptyState title="All clear" body="No broken links or missing calls to action in your recent descriptions." /> : null}
      </div>
    </>
  );
}
