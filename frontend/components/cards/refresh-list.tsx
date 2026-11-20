"use client";

import { InfoNote } from "@/components/states/states";
import { toast } from "@/components/ui/primitives";
import { useRefreshAction } from "@/lib/api/mutations";
import type { RefreshSuggestionView } from "@/lib/api/types";
import { RefreshCard } from "./refresh-card";

// open suggestions first; applied and dismissed ones stay visible as one-line receipts.
// when every open one needs review, the warning shows once above the list instead of on each card
export function RefreshList({ clientId, suggestions }: { clientId: string; suggestions: RefreshSuggestionView[] }) {
  const action = useRefreshAction(clientId);
  const ordered = [...suggestions].sort((a, b) => Number(a.status !== "open") - Number(b.status !== "open"));
  const open = ordered.filter((suggestion) => suggestion.status === "open");
  const reviewOnce = open.length > 1 && open.every((suggestion) => suggestion.review_required);
  return (
    <div className="flex flex-col gap-4">
      {reviewOnce ? <InfoNote tone="warning">Every suggestion here needs medical review before you paste it. Health wording must be checked by your team.</InfoNote> : null}
      {ordered.map((suggestion) => (
        <RefreshCard
          key={suggestion.id}
          suggestion={suggestion}
          reviewNoted={reviewOnce}
          pending={action.isPending && action.variables?.id === suggestion.id}
          onApplied={(appliedText) =>
            action.mutate(
              { id: suggestion.id, action: "applied", appliedText },
              {
                onSuccess: () => toast.success("Marked applied", { description: "We'll compare this video's next 30 days with the 30 before." }),
                onError: () => toast.error("Couldn't save that. Try again."),
              },
            )
          }
          onDismiss={() => action.mutate({ id: suggestion.id, action: "dismiss" }, { onError: () => toast.error("Couldn't dismiss that. Try again.") })}
        />
      ))}
    </div>
  );
}
