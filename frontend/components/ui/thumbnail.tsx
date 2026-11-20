"use client";

import { RiPlayFill } from "@remixicon/react";
import { useState } from "react";
import { cx } from "@/lib/cx";

interface ThumbnailProps {
  videoId: string;
  title: string;
  url?: string | null;
  channelTitle?: string;
  badge?: string;
  linked?: boolean;
  className?: string;
}

// youtube thumbnails are hotlinked unaltered (never proxied or re-encoded); missing ones fall back to a neutral tile
export function Thumbnail({ videoId, title, url, channelTitle, badge, linked = true, className }: ThumbnailProps) {
  const [failed, setFailed] = useState(false);
  const showImage = Boolean(url) && !failed;
  const frameClass = cx("group relative block aspect-video overflow-hidden rounded-control bg-secondary", className);
  const content = (
    <>
      {showImage ? (
        // eslint-disable-next-line @next/next/no-img-element -- next/image would store derived copies of youtube thumbnails
        <img src={url ?? undefined} alt={title} loading="lazy" decoding="async" className="size-full object-cover" onError={() => setFailed(true)} />
      ) : (
        <span className="flex size-full flex-col items-center justify-center gap-1 bg-gradient-to-br from-secondary to-muted px-2 text-center">
          <RiPlayFill aria-hidden className="size-5 text-subtle-foreground transition-colors group-hover:text-foreground" />
          {channelTitle ? <span className="line-clamp-1 text-caption-2-medium text-muted-foreground">{channelTitle}</span> : null}
        </span>
      )}
      {badge ? (
        <span className="absolute right-1 bottom-1 rounded-sm bg-foreground/85 px-1 text-caption-2-medium text-background tabular-nums">{badge}</span>
      ) : null}
    </>
  );
  if (!linked) return <span className={frameClass}>{content}</span>;
  return (
    <a href={`https://www.youtube.com/watch?v=${videoId}`} target="_blank" rel="noreferrer" title={title} className={frameClass}>
      {content}
    </a>
  );
}
