"use client";

import { cx } from "@/lib/cx";
import type { ChannelResolveOut } from "@/lib/api/types";
import { formatCompact } from "@/lib/format";

interface OwnershipPickerProps {
  candidates: ChannelResolveOut["candidates"];
  selected: string | null;
  onSelect: (channelId: string | null) => void;
}

// never auto-attach a low-confidence match: a sister brand can share the business name
export function OwnershipPicker({ candidates, selected, onSelect }: OwnershipPickerProps) {
  return (
    <div role="radiogroup" aria-label="Which channel is yours?" className="flex flex-col gap-2">
      {candidates.map(({ channel, ownership_evidence, confidence }) => (
        <button
          key={channel.channel_id}
          type="button"
          role="radio"
          aria-checked={selected === channel.channel_id}
          onClick={() => onSelect(channel.channel_id)}
          className={cx(
            "flex items-start gap-3 rounded-card border bg-card p-4 text-left transition-colors",
            selected === channel.channel_id ? "border-foreground" : "border-border hover:border-border-strong",
          )}
        >
          <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-full bg-secondary font-heading text-muted-foreground">
            {channel.title.charAt(0)}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-body-medium text-foreground">
              {channel.title} <span className="text-muted-foreground">{channel.handle}</span>
            </span>
            <span className="block text-caption-1-regular text-muted-foreground">
              {formatCompact(channel.subscriber_count)} subscribers · {confidence} confidence
            </span>
            <span className="mt-1 block text-caption-1-regular text-foreground">{ownership_evidence.join(" · ")}</span>
          </span>
        </button>
      ))}
      <button
        type="button"
        role="radio"
        aria-checked={selected === null}
        onClick={() => onSelect(null)}
        className={cx("rounded-card border bg-card p-3 text-left text-body-regular", selected === null ? "border-foreground" : "border-border")}
      >
        None of these. Continue with my website only.
      </button>
    </div>
  );
}
