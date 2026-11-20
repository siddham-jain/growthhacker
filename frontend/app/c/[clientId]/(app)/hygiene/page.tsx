"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshList } from "@/components/cards/refresh-list";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, InfoNote, LoadingBlock } from "@/components/states/states";
import { hygieneQuery } from "@/lib/api/queries";
import { useClientId } from "@/lib/use-client-id";

export default function HygienePage() {
  const clientId = useClientId();
  const refresh = useQuery(hygieneQuery(clientId));
  const fixes = refresh.data?.data.items ?? [];
  return (
    <>
      <PageHeader title="Channel hygiene" subtitle="Small description fixes: missing calls to action, broken links. Each follows a fixed rule, so there are no reference videos." controls="none" />
      <div className="flex flex-col gap-4">
        <InfoNote>{refresh.data?.data.headline ?? "These are rule-based fixes, kept apart from recommendations because they don't rest on outlier evidence."}</InfoNote>
        {refresh.isPending ? <LoadingBlock /> : null}
        {fixes.length ? <RefreshList clientId={clientId} suggestions={fixes} /> : null}
        {refresh.data && !fixes.length ? <EmptyState title="All clear" body="No broken links or missing calls to action in your recent descriptions." /> : null}
      </div>
    </>
  );
}
