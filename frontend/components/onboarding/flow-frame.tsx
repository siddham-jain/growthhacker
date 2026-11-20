"use client";

import { RiArrowDownSLine, RiArrowUpSLine, RiCornerDownLeftLine } from "@remixicon/react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cx } from "@/lib/cx";
import type { Screen, ScreenId } from "@/lib/onboarding/flow";
import { stepProgress } from "@/lib/onboarding/flow";

export function FlowProgress({ screens, current, onJump }: { screens: Screen[]; current: ScreenId; onJump: (screen: ScreenId) => void }) {
  const steps = stepProgress(screens, current);
  const currentIndex = screens.findIndex((screen) => screen.id === current);
  return (
    <nav aria-label="Confirm progress" className="sticky top-0 z-10 -mx-4 bg-background/85 px-4 pt-2 pb-4 backdrop-blur sm:-mx-8 sm:px-8">
      <ol className="mx-auto grid max-w-3xl grid-cols-3 gap-3">
        {steps.map((step, index) => {
          const first = screens.findIndex((screen) => screen.step === step.id);
          const reachable = first <= currentIndex;
          const fill = current === "review" ? 1 : step.done;
          return (
            <li key={step.id}>
              <button
                type="button"
                disabled={!reachable}
                aria-current={step.active ? "step" : undefined}
                onClick={() => onJump(screens[first].id)}
                className="group flex w-full flex-col gap-2 text-left disabled:cursor-default"
              >
                <span className={cx("text-eyebrow transition-colors", step.active ? "text-foreground" : "text-muted-foreground", reachable && !step.active && "group-hover:text-foreground")}>
                  <span className="font-mono">{index + 1}</span> {step.label}
                </span>
                <span aria-hidden className="block h-0.5 overflow-hidden rounded-full bg-border">
                  <span className="block h-full rounded-full bg-foreground transition-[width] duration-500 ease-out motion-reduce:transition-none" style={{ width: `${Math.round(fill * 100)}%` }} />
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

interface FlowScreenProps {
  eyebrow: string;
  title: string;
  helper: ReactNode;
  children: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  onBack?: () => void;
  blocker?: string | null;
  busy?: boolean;
}

// one question per screen: large serif question, a short helper line, the segment, then "looks right"
export function FlowScreen({ eyebrow, title, helper, children, confirmLabel = "Looks right", onConfirm, onBack, blocker, busy }: FlowScreenProps) {
  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-3">
        <p className="text-eyebrow text-muted-foreground">{eyebrow}</p>
        <h1 className="font-display text-[2.25rem] leading-[1.06] tracking-[-0.02em] text-foreground sm:text-[2.75rem]">{title}</h1>
        <p className="max-w-xl text-body-regular text-muted-foreground">{helper}</p>
      </header>
      <div>{children}</div>
      <footer className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-4">
          <Button variant="primary" size="medium" disabled={Boolean(blocker) || busy} onClick={onConfirm} trailingIcon={RiCornerDownLeftLine}>
            {busy ? "Saving…" : confirmLabel}
          </Button>
          {blocker ? null : (
            <span aria-hidden className="hidden text-caption-1-regular text-muted-foreground sm:inline">
              press <span className="font-medium text-foreground">Enter ↵</span>
            </span>
          )}
          {onBack ? (
            <button type="button" onClick={onBack} className="ml-auto text-caption-1-regular text-muted-foreground hover:text-foreground">
              Back <span className="hidden sm:inline">(Shift + Enter)</span>
            </button>
          ) : null}
        </div>
        {blocker ? (
          <p role="status" className="text-body-regular text-warning">
            {blocker}
          </p>
        ) : null}
      </footer>
    </div>
  );
}

export function FlowArrows({ onBack, onNext, canBack, canNext }: { onBack: () => void; onNext: () => void; canBack: boolean; canNext: boolean }) {
  const style = "grid size-9 place-items-center text-foreground transition-colors hover:bg-secondary disabled:text-subtle-foreground disabled:hover:bg-transparent";
  return (
    <div className="fixed right-4 bottom-4 z-10 hidden overflow-hidden rounded-control border border-border bg-card shadow-card sm:flex sm:right-8 sm:bottom-6">
      <button type="button" aria-label="Previous question" disabled={!canBack} onClick={onBack} className={style}>
        <RiArrowUpSLine aria-hidden className="size-5" />
      </button>
      <span aria-hidden className="w-px bg-border" />
      <button type="button" aria-label="Next question" disabled={!canNext} onClick={onNext} className={style}>
        <RiArrowDownSLine aria-hidden className="size-5" />
      </button>
    </div>
  );
}
