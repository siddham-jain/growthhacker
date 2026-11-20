"use client";

import type { ReactNode } from "react";
import { ActivityPill } from "./activity";
import { ViewControls } from "./view-controls";

interface PageHeaderProps {
  title: ReactNode;
  subtitle: string;
  eyebrow?: ReactNode;
  controls?: "window" | "window-lens" | "none";
  actions?: ReactNode;
}

export function PageHeader({ title, subtitle, eyebrow, controls = "window-lens", actions }: PageHeaderProps) {
  return (
    <header className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div className="min-w-0">
        {eyebrow}
        <h1 className="text-title text-foreground">{title}</h1>
        <p className="mt-1 max-w-2xl text-body-regular text-muted-foreground">{subtitle}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {actions}
        {controls === "none" ? null : <ViewControls showLens={controls === "window-lens"} />}
        <ActivityPill />
      </div>
    </header>
  );
}
