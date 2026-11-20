"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { Button, LinkButton } from "@/components/ui/button";
import { Drawer, DrawerBody, DrawerHeader, DrawerTrigger, StatusDot, TaskRows, type TaskRow } from "@/components/ui/primitives";
import { runsQuery } from "@/lib/api/queries";
import type { PipelineRun, RunKind } from "@/lib/api/types";
import { formatRelative } from "@/lib/format";

const KIND_LABELS: Record<RunKind, string> = {
  onboarding: "Setup",
  analysis: "Full analysis",
  daily_refresh: "Daily refresh",
  news_scan: "News scan",
  cards: "Writing cards",
  recrawl: "Re-reading website",
};

const ACTIVE = new Set(["queued", "running"]);

function toRow(run: PipelineRun): TaskRow {
  return {
    key: run.id,
    label: KIND_LABELS[run.kind],
    amount: run.started_at ? formatRelative(run.started_at) : "queued",
    status: ACTIVE.has(run.status) ? "running" : "done",
    step: Math.round(run.percent),
    details: [
      { label: "Status", meta: run.status.replace("_", " ") },
      { label: "Window", meta: `${run.window_days} days` },
      ...run.notices.map((notice) => ({ label: notice.message, meta: notice.code })),
    ],
  };
}

export function ActivityPill() {
  const { clientId } = useParams<{ clientId: string }>();
  const runs = useQuery({ ...runsQuery(clientId), refetchInterval: (query) => (query.state.data?.items.some((run) => ACTIVE.has(run.status)) ? 3_000 : false) });
  const items = runs.data?.items ?? [];
  const active = items.find((run) => ACTIVE.has(run.status));
  return (
    <DrawerTrigger>
      <Button variant="secondary" size="small" aria-label="Activity">
        <span className="inline-flex items-center gap-2">
          <StatusDot color={active ? "indigo" : "green"} />
          {active ? `Analysing · ${Math.round(active.percent)}%` : "Up to date"}
        </span>
      </Button>
      <Drawer size="md" aria-label="Activity">
        <DrawerHeader title="Activity" description="Recent runs for this client. Runs keep going if you leave." />
        <DrawerBody>
          <div className="flex flex-col gap-4">
            {active ? (
              <LinkButton href={`/c/${clientId}/setup/${active.kind === "onboarding" ? "analyze" : "analysis"}?run=${active.id}`} variant="primary">
                Watch the live analysis
              </LinkButton>
            ) : null}
            {items.length ? <TaskRows variant="List" rows={items.slice(0, 10).map(toRow)} /> : <p className="text-body-regular text-muted-foreground">No runs yet.</p>}
          </div>
        </DrawerBody>
      </Drawer>
    </DrawerTrigger>
  );
}
