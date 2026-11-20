"use client";

import { useSearchParams } from "next/navigation";
import { LiveAnalysis } from "@/components/analysis/live-analysis";
import { EmptyState } from "@/components/states/states";
import { useClientId } from "@/lib/use-client-id";

export default function AnalysisPage() {
  const clientId = useClientId();
  const runId = useSearchParams().get("run");
  if (!runId) {
    return <EmptyState title="No analysis running" body="Your latest results are on the Today page." />;
  }
  return (
    <LiveAnalysis
      key={runId}
      runId={runId}
      step="04 · First analysis"
      headline="Building your first brief."
      subtitle="We compare every recent video to its own channel's normal at the same age, then write recommendations only where the evidence holds."
      doneHref={`/c/${clientId}/today?first=1`}
      doneLabel="Open your first brief"
    />
  );
}
