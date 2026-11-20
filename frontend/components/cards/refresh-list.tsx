"use client";

import { toast } from "@/components/ui/primitives";
import { useRefreshAction } from "@/lib/api/mutations";
import type { RefreshSuggestionView } from "@/lib/api/types";
import { RefreshCard } from "./refresh-card";

// open suggestions first; applied and dismissed ones stay visible as one-line receipts
export function RefreshList({ clientId, suggestions }: { clientId: string; suggestions: RefreshSuggestionView[] }) {
  const action = useRefreshAction(clientId);
  const ordered = [...suggestions].sort((a, b) => Number(a.status !== "open") - Number(b.status !== "open"));
  return (
    <div className="flex flex-col gap-4">
      {ordered.map((suggestion) => (
        <RefreshCard
          key={suggestion.id}
          suggestion={suggestion}
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
