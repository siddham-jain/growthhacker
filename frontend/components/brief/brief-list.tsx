import Link from "next/link";
import { RiArrowRightLine } from "@remixicon/react";
import { Chip } from "@/components/ui/primitives";
import type { BriefItem } from "@/lib/brief";
import { formatCountdown } from "@/lib/format";

export function BriefList({ items, now, startAt = 1 }: { items: BriefItem[]; now: number; startAt?: number }) {
  return (
    <ol className="flex flex-col gap-2.5">
      {items.map((item, index) => (
        <li key={item.id}>
          <Link href={item.href} className="group flex items-center gap-3 rounded-card bg-card px-4 py-3.5 shadow-card transition-shadow hover:shadow-raised">
            <span aria-hidden className="grid size-6 shrink-0 place-items-center rounded-full bg-primary text-caption-1-semibold text-primary-foreground">
              {index + startAt}
            </span>
            <span className="min-w-0 flex-1">
              <span className="line-clamp-2 text-headline text-foreground">{item.headline}</span>
              <span className="mt-0.5 line-clamp-1 text-caption-1-regular text-muted-foreground">{item.evidenceLine}</span>
            </span>
            {item.expiresAt ? (
              <Chip variant="caption" color="orange" className="hidden sm:inline-flex">
                {formatCountdown(item.expiresAt, now)}
              </Chip>
            ) : null}
            <span className="hidden items-center gap-1 rounded-full border border-border px-3 py-1 text-caption-1-medium text-foreground transition-colors group-hover:border-border-strong sm:inline-flex">
              {item.actionLabel}
              <RiArrowRightLine aria-hidden className="size-3.5" />
            </span>
          </Link>
        </li>
      ))}
    </ol>
  );
}
