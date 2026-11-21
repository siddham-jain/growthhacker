"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { LiveAnalysis } from "@/components/analysis/live-analysis";
import { EmptyState } from "@/components/states/states";
import { clientQuery } from "@/lib/api/queries";
import { reviewReady } from "@/lib/runs/stages";
import { useClientId } from "@/lib/use-client-id";

export default function AnalyzePage() {
  const clientId = useClientId();
  const client = useQuery(clientQuery(clientId));
  const runId = useSearchParams().get("run") ?? client.data?.active_run_id ?? null;
  if (!runId) {
    return <EmptyState title="No analysis running" body="Start from your clients list to set up a business." />;
  }
  return (
    <LiveAnalysis
      key={runId}
      runId={runId}
      step="02 · Live analysis"
      headline={`Getting to know ${client.data?.name ?? "your business"}.`}
      subtitle="Watch what we read and find. Every row below is a real step, not an animation."
      doneHref={`/c/${clientId}/setup/confirm`}
      doneLabel="Review what we found"
      readyEarly={reviewReady}
      earlyNote="Your business profile is ready. Opening the review now; competitors keep loading while you check it."
    />
  );
}
