import type { RunKind } from "@/lib/api/types";

// keyed by the backend's stage names (backend/app/pipeline/definitions.py)
export const STAGE_LABELS: Record<string, string> = {
  resolve_inputs: "Checking your website and channel",
  crawl_site: "Reading your website",
  prefetch_own_channel: "Fetching your channel's videos",
  build_profile: "Understanding your business",
  discover_competitors: "Finding competitors",
  seed_topic_refs: "Turning your offerings into topics",
  finish_onboarding: "Preparing your setup",
  snapshot_config: "Locking this run's settings",
  ingest: "Fetching recent videos",
  niche_discovery: "Finding channels in your niche",
  own_analytics: "Reading your owner analytics",
  news: "Scanning the news",
  competitor_drift: "Checking your competitor set",
  enrich: "Reading titles and thumbnails",
  topics: "Matching videos to your topics",
  analytics: "Comparing videos to their normal",
  recommend: "Choosing what to recommend",
  write_cards: "Writing recommendations",
  refresh: "Checking your descriptions",
  finalize: "Publishing your results",
};

// display order for known stages; stages run as a graph, so several can be in flight at once
export const RUN_STAGES: Partial<Record<RunKind, string[]>> = {
  onboarding: ["resolve_inputs", "crawl_site", "prefetch_own_channel", "build_profile", "discover_competitors", "seed_topic_refs", "finish_onboarding"],
  analysis: ["snapshot_config", "ingest", "niche_discovery", "own_analytics", "news", "competitor_drift", "enrich", "topics", "analytics", "recommend", "write_cards", "refresh", "finalize"],
};
