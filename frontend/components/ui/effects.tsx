"use client";

import { BorderBeam } from "border-beam";
import { ThinkingOrb, type OrbState } from "thinking-orbs";
import type { ReactNode } from "react";
import { usePrefersReducedMotion, useThemeClass } from "@/hooks/use-media-state";

// the glow is reserved for the single active run card and switches off with reduced motion
export function ActiveBeam({ active, children }: { active: boolean; children: ReactNode }) {
  const reduce = usePrefersReducedMotion();
  const theme = useThemeClass();
  return (
    <BorderBeam active={active && !reduce} colorVariant="ocean" theme={theme} size="md" strength={0.7}>
      {children}
    </BorderBeam>
  );
}

export function AnalysisOrb({ state = "working", size = 32, label }: { state?: OrbState; size?: 20 | 32 | 64; label: string }) {
  const reduce = usePrefersReducedMotion();
  return <ThinkingOrb state={state} size={size} paused={Boolean(reduce)} role="img" aria-label={label} />;
}
