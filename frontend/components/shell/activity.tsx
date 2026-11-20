"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusDot } from "@/components/ui/primitives";
import { runsQuery } from "@/lib/api/queries";
import { isActiveRun } from "./run-status";

// the drawer needs react-aria, so it loads on first open; once mounted it stays for the exit animation
const ActivityDrawer = dynamic(() => import("./activity-drawer"), { ssr: false });

export function ActivityPill() {
  const { clientId } = useParams<{ clientId: string }>();
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const runs = useQuery({ ...runsQuery(clientId), refetchInterval: (query) => (query.state.data?.items.some(isActiveRun) ? 3_000 : false) });
  const items = runs.data?.items ?? [];
  const active = items.find(isActiveRun);
  return (
    <>
      <Button
        variant="secondary"
        size="small"
        aria-haspopup="dialog"
        onClick={() => {
          setMounted(true);
          setOpen(true);
        }}
      >
        <span className="inline-flex items-center gap-2">
          <StatusDot color={active ? "indigo" : "green"} />
          {active ? `Analysing · ${Math.round(active.percent)}%` : "Up to date"}
        </span>
      </Button>
      {mounted ? <ActivityDrawer clientId={clientId} runs={items} isOpen={open} onOpenChange={setOpen} /> : null}
    </>
  );
}
