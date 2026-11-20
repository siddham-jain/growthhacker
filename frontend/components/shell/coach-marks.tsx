"use client";

import { useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { usePreferences } from "@/lib/prefs";

export interface CoachStep {
  id: string;
  target: string;
  text: string;
}

interface Position {
  top: number;
  left: number;
}

function locate(selector: string): Position | null {
  const element = document.querySelector(selector);
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  if (!rect.width && !rect.height) return null;
  const width = 280;
  return { top: rect.bottom + 10, left: Math.min(Math.max(12, rect.left), window.innerWidth - width - 12) };
}

// at most three one-time hints, one at a time; steps whose target isn't on screen are skipped
export function CoachMarks({ steps, active }: { steps: CoachStep[]; active: boolean }) {
  const { prefs, dismissHint } = usePreferences();
  const pending = steps.filter((step) => !prefs.dismissed_hints.includes(step.id));
  const step = active ? pending[0] : undefined;
  const [position, setPosition] = useState<Position | null>(null);
  const titleId = useId();

  useEffect(() => {
    if (!step) return;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setPosition(locate(step.target)));
    };
    const timer = window.setTimeout(update, 400);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") steps.forEach((item) => dismissHint(item.id));
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [step, steps, dismissHint]);

  if (!step || !position) return null;
  const index = steps.findIndex((item) => item.id === step.id);
  const last = pending.length === 1;
  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      className="fixed z-40 w-[280px] animate-reveal rounded-card bg-primary p-4 text-primary-foreground shadow-overlay"
      style={{ top: position.top, left: position.left }}
    >
      <span aria-hidden className="absolute -top-1.5 left-6 size-3 rotate-45 bg-primary" />
      <p className="text-caption-1-medium opacity-70">
        Tip {index + 1} of {steps.length}
      </p>
      <p id={titleId} className="mt-1 text-body-regular">
        {step.text}
      </p>
      <div className="mt-3 flex items-center justify-between gap-2">
        <button type="button" className="text-caption-1-medium underline-offset-2 opacity-70 hover:underline hover:opacity-100" onClick={() => steps.forEach((item) => dismissHint(item.id))}>
          Skip tips
        </button>
        <Button variant="secondary" size="xs" onClick={() => dismissHint(step.id)} autoFocus>
          {last ? "Got it" : "Next"}
        </Button>
      </div>
    </div>
  );
}
