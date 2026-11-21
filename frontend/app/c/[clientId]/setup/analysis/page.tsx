"use client";

import { useSearchParams } from "next/navigation";
import { LiveAnalysis } from "@/components/analysis/live-analysis";
import { EmptyState } from "@/components/states/states";
import { useClientId } from "@/lib/use-client-id";

export default function AnalysisPage() {
  const clientId = useClientId();
  const runId = useSearchParams().get("run");
  if (!runId) {
    return <EmptyState title="No analysis running" body="Your latest video ideas are on the Next videos page." />;
  }
  return (
    <LiveAnalysis
      key={runId}
      runId={runId}
      step="04 · First analysis"
      headline="Finding what to post next."
      subtitle="We check which of your keywords are trending on YouTube, compare every recent video to its own channel's normal, then write ideas only where the evidence holds."
      doneHref={`/c/${clientId}/ideas`}
      doneLabel="See your video ideas"
    />
  );
}
