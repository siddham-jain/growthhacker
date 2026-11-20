"use client";

import { BorderBeam } from "border-beam";
import { useReducedMotion } from "motion/react";
import { ThinkingOrb, type OrbState } from "thinking-orbs";
import type { ReactNode } from "react";
import { useThemeMode } from "@/components/kits/boardui/application/theme/theme-toggle";

// the glow is reserved for the single active run card and switches off with reduced motion
export function ActiveBeam({ active, children }: { active: boolean; children: ReactNode }) {
  const reduce = useReducedMotion();
  const theme = useThemeMode();
  return (
    <BorderBeam active={active && !reduce} colorVariant="ocean" theme={theme} size="md" strength={0.7}>
      {children}
    </BorderBeam>
  );
}

export function AnalysisOrb({ state = "working", size = 32, label }: { state?: OrbState; size?: 20 | 32 | 64; label: string }) {
  const reduce = useReducedMotion();
  return <ThinkingOrb state={state} size={size} paused={Boolean(reduce)} role="img" aria-label={label} />;
}
