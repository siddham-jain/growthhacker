"use client";

import { RiArrowDownSLine, RiBookmarkFill, RiBookmarkLine, RiCheckLine, RiCloseLine, RiSearchEyeLine } from "@remixicon/react";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverHeader, PopoverTrigger, toast } from "@/components/ui/primitives";
import { TextField } from "@/components/ui/text-field";
import type { FeedbackIn, FeedbackReason, RecommendationFeedback } from "@/lib/api/types";
import { FEEDBACK_REASON_LABELS } from "@/lib/labels";
import { videoIdFromUrl } from "@/lib/youtube-url";

interface FeedbackActionsProps {
  feedback: RecommendationFeedback | null | undefined;
  onFeedback: (input: FeedbackIn) => void;
  onEvidence?: () => void;
  reasons?: FeedbackReason[];
  allowDone?: boolean;
  meta?: string;
}

const DEFAULT_REASONS: FeedbackReason[] = ["not_our_business", "already_covered", "cannot_produce_format", "evidence_wrong", "other"];

function MarkDone({ done, onFeedback }: { done: boolean; onFeedback: (input: FeedbackIn) => void }) {
  const [open, setOpen] = useState(false);
  const [link, setLink] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    const videoId = link.trim() ? videoIdFromUrl(link) : null;
    if (link.trim() && !videoId) {
      setError("Paste a YouTube video link, or leave this empty.");
      return;
    }
    onFeedback({ status: "done", linked_video_id: videoId });
    setOpen(false);
    toast.success("Marked done", { description: videoId ? "We'll track how your video does against your normal." : "Link the video later to track how it does." });
  }

  if (done) {
    return (
      <Button variant="secondary" size="xs" leadingIcon={RiCheckLine} aria-pressed onClick={() => onFeedback({ status: null })}>
        Done
      </Button>
    );
  }
  return (
    <PopoverTrigger isOpen={open} onOpenChange={setOpen}>
      <Button variant="secondary" size="xs" leadingIcon={RiCheckLine}>
        Mark done
      </Button>
      <Popover placement="top start" aria-label="Mark done">
        <form onSubmit={submit} className="flex flex-col gap-3">
          <PopoverHeader title="Made this video?" description="Link it and we'll compare it to your own normal at the same age." />
          <TextField label="Your video link (optional)" value={link} onChange={(event) => setLink(event.target.value)} placeholder="youtube.com/watch?v=…" error={error} autoFocus />
          <Button type="submit" variant="primary" size="small" className="w-fit">
            Mark done
          </Button>
        </form>
      </Popover>
    </PopoverTrigger>
  );
}

function Dismiss({ reasons, onFeedback }: { reasons: FeedbackReason[]; onFeedback: (input: FeedbackIn) => void }) {
  const [open, setOpen] = useState(false);
  function choose(reason: FeedbackReason) {
    setOpen(false);
    onFeedback({ status: "dismissed", reason });
    toast("Dismissed", {
      description: `${FEEDBACK_REASON_LABELS[reason]}. We'll show fewer ideas like this.`,
      actions: [{ label: "Undo", onClick: () => onFeedback({ status: null }) }],
    });
  }
  return (
    <PopoverTrigger isOpen={open} onOpenChange={setOpen}>
      <Button variant="secondary" size="xs" leadingIcon={RiCloseLine} trailingIcon={RiArrowDownSLine}>
        Dismiss
      </Button>
      <Popover placement="top start" aria-label="Why dismiss?" className="w-64">
        <p className="px-1 text-caption-1-medium text-muted-foreground">Why isn&apos;t this right?</p>
        <ul className="flex flex-col">
          {reasons.map((reason) => (
            <li key={reason}>
              <button type="button" onClick={() => choose(reason)} className="w-full rounded-control px-2 py-1.5 text-left text-body-regular text-foreground hover:bg-muted">
                {FEEDBACK_REASON_LABELS[reason]}
              </button>
            </li>
          ))}
        </ul>
      </Popover>
    </PopoverTrigger>
  );
}

// save, done and dismiss feed the per-client feedback loop; evidence opens the deepest disclosure level
export function FeedbackActions({ feedback, onFeedback, onEvidence, reasons = DEFAULT_REASONS, allowDone = true, meta }: FeedbackActionsProps) {
  const status = feedback?.status ?? null;
  return (
    <footer className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
      <Button
        variant="secondary"
        size="xs"
        leadingIcon={status === "saved" ? RiBookmarkFill : RiBookmarkLine}
        aria-pressed={status === "saved"}
        onClick={() => {
          onFeedback({ status: status === "saved" ? null : "saved" });
          if (status !== "saved") toast.success("Saved", { description: "Find it under Saved in Next videos." });
        }}
      >
        {status === "saved" ? "Saved" : "Save"}
      </Button>
      {allowDone ? <MarkDone done={status === "done"} onFeedback={onFeedback} /> : null}
      <Dismiss reasons={reasons} onFeedback={onFeedback} />
      {onEvidence ? (
        <Button variant="ghost" size="xs" leadingIcon={RiSearchEyeLine} onClick={onEvidence} className="sm:ml-auto" data-coach="evidence">
          Evidence
        </Button>
      ) : null}
      {meta ? <span className="w-full text-caption-1-regular text-subtle-foreground sm:w-auto">{meta}</span> : null}
    </footer>
  );
}
