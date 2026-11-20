"use client";

import { Button } from "@/components/ui/button";
import type { RunNotice } from "@/lib/api/types";
import { useViewParams } from "@/lib/url-state";

// widening is a view change only; the client's default window changes in settings
export function ThinDataPrompt({ notice }: { notice: RunNotice }) {
  const { setWindow } = useViewParams();
  const widenTo = notice.suggest?.widen_window_to;
  return (
    <div className="flex flex-col gap-3 rounded-card border border-dashed border-border-strong bg-card px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-body-regular text-foreground">{notice.message}</p>
      {widenTo ? (
        <Button variant="primary" size="small" onClick={() => setWindow(widenTo)}>
          Widen to {widenTo}?
        </Button>
      ) : null}
    </div>
  );
}
