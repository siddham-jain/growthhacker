"use client";

import { motion } from "motion/react";
import Link from "next/link";
import type { ReactNode } from "react";
import { FolderCard } from "@/components/ui/primitives";
import { Thumbnail } from "@/components/ui/thumbnail";

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

function FannedPreviews({ previews, lifted, channelTitle }: { previews: FolderPreview[]; lifted: boolean; channelTitle: string }) {
  if (!previews.length) {
    return (
      <motion.div
        animate={{ y: lifted ? -8 : 0 }}
        className="absolute top-[12%] left-1/2 grid size-14 -translate-x-1/2 place-items-center rounded-full bg-card font-heading text-xl text-muted-foreground shadow-raised"
      >
        {channelTitle.charAt(0)}
      </motion.div>
    );
  }
  return (
    <div className="absolute inset-x-0 top-[10%] flex justify-center">
      {previews.slice(0, 3).map((preview, index) => (
        <motion.div
          key={preview.videoId}
          className="absolute w-[46%] rounded-control bg-card p-0.5 shadow-raised"
          animate={{ rotate: lifted ? FAN[index].rotate * 1.3 : FAN[index].rotate, x: FAN[index].x, y: lifted ? -10 : 0 }}
          transition={{ type: "spring", stiffness: 380, damping: 32 }}
        >
          <Thumbnail videoId={preview.videoId} title={preview.title} channelTitle={channelTitle} badge={preview.badge} linked={false} />
        </motion.div>
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
      media={(lifted) => <FannedPreviews previews={previews} lifted={lifted} channelTitle={name} />}
      {...folder}
    />
  );
  if (!href) return card;
  return (
    <Link href={href} aria-label={`${name}: open competitor analysis`} className="block rounded-[24px] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
      {card}
    </Link>
  );
}
