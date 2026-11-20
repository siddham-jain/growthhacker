"use client";

import { RiCheckLine } from "@remixicon/react";
import { cx } from "@/lib/cx";

interface TopicOption {
  id: string;
  text: string;
}

interface TopicReviewProps {
  label: string;
  options: TopicOption[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}

export function TopicReview({ label, options, selected, onToggle }: TopicReviewProps) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 text-body-medium text-foreground">
        {label} <span className="text-muted-foreground">· {selected.size} of {options.length} selected</span>
      </legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const active = selected.has(option.id);
          return (
            <button
              key={option.id}
              type="button"
              aria-pressed={active}
              onClick={() => onToggle(option.id)}
              className={cx(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-body-regular transition-colors",
                active ? "border-foreground bg-foreground text-background" : "border-border bg-card text-muted-foreground hover:border-border-strong",
              )}
            >
              {active ? <RiCheckLine aria-hidden className="size-4" /> : null}
              {option.text}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
