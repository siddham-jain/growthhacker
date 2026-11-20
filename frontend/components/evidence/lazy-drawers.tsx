"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import type { VideoMetricsView } from "@/lib/api/types";
import { useEvidenceParam } from "@/lib/url-state";

// the drawers need react-aria, so they load the first time evidence is opened and then stay mounted
const VideoDrawer = dynamic(() => import("./evidence-drawer").then((module) => module.VideoEvidenceDrawer), { ssr: false });
const RecommendationDrawer = dynamic(() => import("./evidence-drawer").then((module) => module.RecommendationEvidenceDrawer), { ssr: false });

function useOpenedOnce(open: boolean): boolean {
  const [opened, setOpened] = useState(open);
  if (open && !opened) setOpened(true);
  return opened;
}

export function VideoEvidenceDrawer({ video, onClose }: { video: VideoMetricsView | null; onClose: () => void }) {
  return useOpenedOnce(Boolean(video)) ? <VideoDrawer video={video} onClose={onClose} /> : null;
}

export function RecommendationEvidenceDrawer({ clientId }: { clientId: string }) {
  const { evidenceId } = useEvidenceParam();
  return useOpenedOnce(Boolean(evidenceId)) ? <RecommendationDrawer clientId={clientId} /> : null;
}
