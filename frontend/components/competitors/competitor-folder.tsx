"use client";

import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";
import { FolderCard } from "@/components/ui/primitives";
import { Thumbnail } from "@/components/ui/thumbnail";
import { cx } from "@/lib/cx";

export interface FolderPreview {
  videoId: string;
  title: string;
  badge?: string;
}

const FAN = [
  { rotate: -9, x: "-38%" },
  { rotate: 0, x: "0%" },
  { rotate: 9, x: "38%" },
];

// the fan opens on hover or focus of the folder; reduced motion keeps it still
const FAN_MOTION = "motion-safe:transition-transform motion-safe:duration-300 motion-safe:ease-[cubic-bezier(0.16,1,0.3,1)]";

function FannedPreviews({ previews, channelTitle }: { previews: FolderPreview[]; channelTitle: string }) {
  if (!previews.length) {
    return (
      <div className={cx("absolute top-[12%] left-1/2 grid size-14 -translate-x-1/2 place-items-center rounded-full bg-card font-heading text-xl text-muted-foreground shadow-raised", FAN_MOTION, "motion-safe:group-hover:-translate-y-2 motion-safe:group-focus-within:-translate-y-2")}>
        {channelTitle.charAt(0)}
      </div>
    );
  }
  return (
    <div className="absolute inset-x-0 top-[10%] flex justify-center">
      {previews.slice(0, 3).map((preview, index) => (
        <div
          key={preview.videoId}
          style={{ "--fan-x": FAN[index].x, "--fan-r": `${FAN[index].rotate}deg`, "--fan-r-open": `${FAN[index].rotate * 1.3}deg` } as CSSProperties}
          className={cx(
            "absolute w-[46%] rounded-control bg-card p-0.5 shadow-raised [transform:translateX(var(--fan-x))_rotate(var(--fan-r))]",
            FAN_MOTION,
            "motion-safe:group-hover:[transform:translateX(var(--fan-x))_translateY(-10px)_rotate(var(--fan-r-open))] motion-safe:group-focus-within:[transform:translateX(var(--fan-x))_translateY(-10px)_rotate(var(--fan-r-open))]",
          )}
        >
          <Thumbnail videoId={preview.videoId} title={preview.title} channelTitle={channelTitle} badge={preview.badge} linked={false} />
        </div>
      ))}
    </div>
  );
}

interface CompetitorFolderProps {
  name: string;
  href?: string;
  previews: FolderPreview[];
  badge?: ReactNode;
  subtitle?: ReactNode;
  count: ReactNode;
  countLabel: ReactNode;
  meta?: ReactNode;
  size?: "sm" | "md";
}

// one interface for every folder in the app; swapping the visual means editing only this file
export function CompetitorFolder({ name, href, previews, size = "md", ...folder }: CompetitorFolderProps) {
  const card = (
    <FolderCard
      title={name}
      size={size}
      media={<FannedPreviews previews={previews} channelTitle={name} />}
      {...folder}
    />
  );
  if (!href) return card;
  return (
    <Link href={href} className="group block rounded-[24px] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
      {card}
      <span className="sr-only">Open competitor analysis</span>
    </Link>
  );
}
