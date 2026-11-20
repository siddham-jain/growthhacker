// fork of zeus ui artemis-folder-card (components/cards/variants/04-folder.tsx, registry v0.4.12, MIT):
// content and media become props, colours come from our tokens instead of artemis-tokens,
// the hover lift also responds to keyboard focus inside the card, and css transitions replace
// the motion library so the competitor screens don't load it
import type { ReactNode } from "react";
import { cx } from "@/lib/cx";

// the expo ease-out the original used for its entrance
const LIFT = "motion-safe:transition-transform motion-safe:duration-300 motion-safe:ease-[cubic-bezier(0.16,1,0.3,1)]";

export interface FolderCardProps {
  title: string;
  subtitle?: ReactNode;
  badge?: ReactNode;
  count: ReactNode;
  countLabel: ReactNode;
  meta?: ReactNode;
  // lifts through `group-hover` / `group-focus-within` classes of its own
  media: ReactNode;
  size?: "sm" | "md";
  className?: string;
}

export function FolderCard({ title, subtitle, badge, count, countLabel, meta, media, size = "md", className }: FolderCardProps) {
  const small = size === "sm";
  const raise = "motion-safe:group-hover:-translate-y-0.5 motion-safe:group-focus-within:-translate-y-0.5";
  return (
    <article
      className={cx(
        "group animate-reveal relative isolate w-full overflow-hidden border border-border bg-card",
        small ? "aspect-[1.25/1] rounded-card" : "aspect-[1.3/1] rounded-[24px]",
        className,
      )}
    >
      <div aria-hidden className="absolute inset-px overflow-hidden rounded-sm">
        <div className={cx("absolute -inset-2 overflow-hidden bg-[linear-gradient(140deg,var(--secondary)_0%,var(--muted)_60%,var(--card)_100%)]", LIFT, "motion-safe:group-hover:scale-[1.035] motion-safe:group-focus-within:scale-[1.035]")}>{media}</div>
      </div>
      {badge ? (
        <div className="absolute top-[7%] right-[5%] z-10 max-w-[48%] truncate rounded-full border border-border bg-card/85 px-2.5 py-1 text-right text-caption-1-medium text-foreground backdrop-blur-sm">
          {badge}
        </div>
      ) : null}
      <div aria-hidden className={cx("pointer-events-none absolute inset-x-0 top-0 bottom-[-2px] z-10", LIFT, raise)}>
        <svg aria-hidden viewBox="0 0 100 100" preserveAspectRatio="none" className="size-full">
          <path d="M 0 100 V 45 C 0 40.5 2.8 38 7 38 H 38 C 41.5 38 43.5 39.2 46 41.8 L 52.5 48.2 C 54.6 50.2 57 51 61 51 H 94 C 98 51 100 53.4 100 57.5 V 100 Z" className="fill-card" />
        </svg>
      </div>
      <div className={cx("pointer-events-none absolute inset-0 z-20", LIFT, raise)}>
        <div className={cx("absolute left-[5%] max-w-[90%]", small ? "top-[44%]" : "top-[43%]")}>
          <h3 className={cx("truncate font-heading font-medium tracking-[-0.025em] text-foreground", small ? "text-[15px] leading-5" : "text-[22px] leading-7")}>{title}</h3>
          {subtitle ? <div className="mt-0.5 truncate text-caption-1-regular text-muted-foreground">{subtitle}</div> : null}
        </div>
        <div className="absolute right-[6%] bottom-[7%] left-[5%] flex items-end justify-between gap-4">
          <div className="flex min-w-0 items-baseline gap-1.5">
            <span className={cx("font-heading font-medium leading-none tracking-[-0.05em] text-foreground tabular-nums", small ? "text-2xl" : "text-4xl")}>{count}</span>
            <span className="text-caption-1-regular text-muted-foreground">{countLabel}</span>
          </div>
          {meta ? <span className="truncate text-right text-caption-1-medium text-foreground tabular-nums">{meta}</span> : null}
        </div>
      </div>
    </article>
  );
}
