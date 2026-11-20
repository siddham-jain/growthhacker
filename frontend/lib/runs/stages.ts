import type { RunKind } from "@/lib/api/types";

export const STAGE_LABELS: Record<string, string> = {
  crawl: "Reading your website",
  profile: "Understanding your business",
  resolve: "Checking your channel",
  discover: "Finding competitors",
  ingest: "Fetching recent videos",
  metrics: "Comparing videos to their normal",
  patterns: "Finding what outliers share",
  news: "Scanning the news",
  write: "Writing recommendations",
  refresh: "Checking your descriptions",
};

// display order for known stages; unknown stages from the server are appended as they arrive
export const RUN_STAGES: Partial<Record<RunKind, string[]>> = {
  onboarding: ["crawl", "profile", "resolve", "discover"],
  analysis: ["ingest", "metrics", "patterns", "news", "write", "refresh"],
};
