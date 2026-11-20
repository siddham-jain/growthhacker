import type { EventPayload, RunEvent, RunEventType, RunKind } from "../types";
import { channels } from "./fixtures/base";
import { recommendations } from "./fixtures/insights";
import { videos } from "./fixtures/videos";

export interface ScriptedEvent {
  delayMs: number;
  type: RunEventType;
  stage?: string;
  status?: RunEvent["status"];
  level?: RunEvent["level"];
  message: string;
  percent?: number;
  counts?: RunEvent["counts"];
  payload?: EventPayload;
}

const SITE = "apollohospitals.com";

function insight(delayMs: number, stage: string, message: string, payload: EventPayload, percent?: number): ScriptedEvent {
  return { delayMs, type: "insight", stage, message, payload, percent };
}

function warn(delayMs: number, stage: string, message: string, payload?: EventPayload): ScriptedEvent {
  return { delayMs, type: "notice", stage, level: "warning", message, payload };
}

function stageStart(delayMs: number, stage: string, message: string): ScriptedEvent {
  return { delayMs, type: "stage.started", stage, status: "running", message };
}

function stageDone(delayMs: number, stage: string, message: string, percent: number, counts?: RunEvent["counts"]): ScriptedEvent {
  return { delayMs, type: "stage.succeeded", stage, status: "succeeded", message, percent, counts };
}

function pageRead(delayMs: number, path: string, title: string, pageType: Extract<EventPayload, { kind: "page_read" }>["page_type"], sourceKind: "reader_proxy" | "search_index" | "archive" | "knowledge_base"): ScriptedEvent {
  const url = sourceKind === "knowledge_base" ? `https://en.wikipedia.org${path}` : `https://www.${SITE}${path}`;
  return insight(delayMs, "crawl", `Read ${title}`, { kind: "page_read", url, path, title, page_type: pageType, source_kind: sourceKind });
}

const onboarding: ScriptedEvent[] = [
  { delayMs: 300, type: "run.started", status: "running", message: "Started setting up Apollo Hospitals", percent: 1 },
  stageStart(500, "crawl", "Reading your website"),
  insight(700, "crawl", `Opening ${SITE}`, { kind: "source_started", tier: 1, source_kind: "site_direct", target: SITE }),
  warn(1100, "crawl", `${SITE} blocked our request (firewall, 403)`, { kind: "source_blocked", source_kind: "site_direct", reason: "Firewall returned 403" }),
  insight(500, "crawl", "Trying a full browser render", { kind: "source_fallback", from: "site_direct", to: "site_rendered" }),
  insight(400, "crawl", `Rendering ${SITE} in a browser`, { kind: "source_started", tier: 2, source_kind: "site_rendered", target: SITE }),
  warn(1300, "crawl", "The site showed a bot challenge, so we didn't continue", { kind: "source_blocked", source_kind: "site_rendered", reason: "Bot challenge page" }),
  insight(500, "crawl", "Routing around the block through a reader service", { kind: "source_fallback", from: "site_rendered", to: "reader_proxy" }, 8),
  insight(400, "crawl", "Reading public copies of your pages", { kind: "source_started", tier: 3, source_kind: "reader_proxy", target: "r.jina.ai" }),
  pageRead(600, "/departments/cardiology", "Cardiology", "service", "reader_proxy"),
  pageRead(350, "/departments/oncology", "Cancer care", "service", "reader_proxy"),
  pageRead(350, "/departments/transplant", "Organ transplant", "service", "reader_proxy"),
  pageRead(350, "/robotic-surgery", "Robotic surgery", "service", "reader_proxy"),
  pageRead(350, "/about-us", "About Apollo", "about", "reader_proxy"),
  pageRead(350, "/locations/chennai", "Chennai", "location", "reader_proxy"),
  pageRead(350, "/locations/hyderabad", "Hyderabad", "location", "reader_proxy"),
  pageRead(350, "/book-appointment", "Book an appointment", "other", "reader_proxy"),
  { delayMs: 200, type: "stage.progress", stage: "crawl", message: "Read 8 pages", percent: 16, counts: { done: 8, total: null, unit: "pages" } },
  insight(600, "crawl", "Still missing some locations and services, checking the search index", { kind: "source_fallback", from: "reader_proxy", to: "search_index" }),
  insight(400, "crawl", `Looking up ${SITE} in a search index`, { kind: "source_started", tier: 4, source_kind: "search_index", target: SITE }),
  pageRead(500, "/proton-cancer-centre", "Proton cancer centre", "service", "search_index"),
  pageRead(350, "/departments/orthopaedics", "Orthopaedics", "service", "search_index"),
  pageRead(350, "/locations/delhi", "New Delhi", "location", "search_index"),
  insight(500, "crawl", "Checking an archived copy for older location pages", { kind: "source_started", tier: 5, source_kind: "archive", target: "web.archive.org" }),
  pageRead(500, "/locations/navi-mumbai", "Navi Mumbai (archived Sep 2025)", "location", "archive"),
  insight(400, "crawl", "Checking Wikipedia and Wikidata", { kind: "source_started", tier: 6, source_kind: "knowledge_base", target: "wikidata.org" }),
  pageRead(450, "/wiki/Apollo_Hospitals", "Apollo Hospitals (Wikipedia)", "about", "knowledge_base"),
  insight(250, "crawl", "Skipped a page over budget", { kind: "page_skipped", url: `https://www.${SITE}/blog/archive`, reason: "budget" }),
  stageDone(400, "crawl", "Read 13 pages from 4 sources", 32, { done: 13, total: 13, unit: "pages" }),
  stageStart(400, "profile", "Understanding your business"),
  insight(900, "profile", "Found 7 offerings", { kind: "profile_field", field: "offerings", count: 7, preview: ["Cardiac sciences", "Cancer care", "Proton therapy", "Organ transplant", "Robotic surgery"], source_kinds: ["reader_proxy", "search_index", "youtube_channel"] }, 40),
  insight(800, "profile", "Found 5 locations", { kind: "profile_field", field: "locations", count: 5, preview: ["Chennai", "Hyderabad", "New Delhi", "Bengaluru", "Navi Mumbai"], source_kinds: ["reader_proxy", "search_index", "archive"] }),
  insight(700, "profile", "Found 4 ways customers reach you", { kind: "profile_field", field: "conversion_actions", count: 4, preview: ["Book an appointment", "Call 1860-500-1066", "Book a health check", "Request a second opinion"], source_kinds: ["reader_proxy", "youtube_channel"] }),
  insight(700, "profile", "Found 3 value propositions", { kind: "profile_field", field: "value_props", count: 3, preview: ["Multi-city JCI-accredited network", "Early proton and robotic adopter"], source_kinds: ["knowledge_base", "reader_proxy", "web_mentions"] }),
  insight(800, "profile", "Drafted 8 buyer questions", { kind: "profile_field", field: "buyer_questions", count: 8, preview: ["How much does bypass surgery cost in Hyderabad?", "Do I need angioplasty or bypass surgery?", "Is proton therapy right for my cancer?"], source_kinds: ["reader_proxy", "search_index"] }),
  stageDone(400, "profile", "Found 7 offerings, 8 buyer questions", 56),
  stageStart(400, "resolve", "Checking your channel"),
  insight(1000, "resolve", "Found your channel through a link on your website", { kind: "channel_resolved", channel_id: channels.apollo.channel_id, title: channels.apollo.title, handle: channels.apollo.handle, thumbnail_url: null, subscriber_count: channels.apollo.subscriber_count, verified_by: "website_link" }, 62),
  stageDone(400, "resolve", "Matched @ApolloHospitalsIndia", 66),
  stageStart(400, "discover", "Finding competitors"),
  ...[channels.fortis, channels.narayana, channels.medanta, channels.manipal, channels.max, channels.practo, channels.doctorExplains].map((channel, index) =>
    insight(index === 0 ? 1100 : 750, "discover", `Found ${channel.title}`, {
      kind: "competitor_candidate",
      candidate_id: `cand_${index}`,
      name: channel.title,
      channel_id: channel.channel_id,
      thumbnail_url: null,
      source: index % 2 === 0 ? "web_search" : "profile",
      evidence_count: 4 - (index % 3),
    }, 70 + index * 4),
  ),
  { delayMs: 500, type: "notice", stage: "discover", level: "info", message: "Left out Apollo 24|7: it belongs to the same group" },
  stageDone(400, "discover", "Found 7 candidates", 98, { done: 7, total: 7, unit: "channels" }),
  { delayMs: 600, type: "run.awaiting_confirmation", status: "awaiting_confirmation", message: "Ready for you to confirm", percent: 100 },
];

const fetchedChannels = [
  [channels.apollo, 31],
  [channels.fortis, 38],
  [channels.narayana, 26],
  [channels.medanta, 44],
  [channels.manipal, 17],
  [channels.max, 21],
  [channels.practo, 64],
] as const;

const analysis: ScriptedEvent[] = [
  { delayMs: 300, type: "run.started", status: "running", message: "Started the first analysis", percent: 1 },
  stageStart(400, "ingest", "Fetching recent videos"),
  ...fetchedChannels.map(([channel, count], index) =>
    insight(index === 0 ? 700 : 450, "ingest", `${channel.title}: ${count} videos in 90 days`, { kind: "videos_fetched", channel_id: channel.channel_id, channel_title: channel.title, count, window_days: 90 }, 4 + index * 3),
  ),
  stageDone(400, "ingest", "Fetched 241 videos from 7 channels", 26, { done: 241, total: 241, unit: "videos" }),
  stageStart(400, "metrics", "Comparing each video to its channel's normal"),
  ...[videos.maxTransplantJourney, videos.fortisBypassCost, videos.heartSmartHindi, videos.narayanaHeartCost, videos.medantaAngioVsBypass].map((item, index) =>
    insight(index === 0 ? 900 : 600, "metrics", `${item.channel_title}: ${item.outlier_ratio}× their normal`, {
      kind: "outlier_found",
      video_id: item.video_id,
      channel_title: item.channel_title,
      title: item.title,
      outlier_ratio: item.outlier_ratio ?? 0,
      views_at_age: item.views_at_age,
    }, 30 + index * 4),
  ),
  stageDone(400, "metrics", "Found 14 outliers across 6 channels", 52),
  stageStart(400, "patterns", "Finding what the outliers share"),
  insight(800, "patterns", "Question titles are 2.1× more common among outliers", { kind: "trait_lift", trait: "Question titles", lift: 2.1, outliers_with_trait: 9, sample: 80 }, 56),
  insight(600, "patterns", "Cost in the title is 2.4× more common among outliers", { kind: "trait_lift", trait: "Cost in the title", lift: 2.4, outliers_with_trait: 6, sample: 80 }),
  stageDone(400, "patterns", "2 patterns hold up", 62),
  stageStart(400, "news", "Scanning health news"),
  insight(700, "news", "Revised CGHS cardiac package rates", { kind: "news_found", news_id: "ns_cghs", headline: "Revised CGHS package rates for cardiac procedures announced", source_domain: "pib.gov.in", published_at: new Date().toISOString() }, 66),
  insight(600, "news", "New proton therapy indication", { kind: "news_found", news_id: "ns_proton", headline: "New proton therapy indication approved for paediatric brain tumours", source_domain: "cdsco.gov.in", published_at: new Date().toISOString() }),
  stageDone(400, "news", "2 stories tie to your offerings", 72),
  stageStart(400, "write", "Writing recommendations"),
  ...recommendations.map((card, index) =>
    insight(index === 0 ? 1100 : 900, "write", `Wrote: ${card.summary}`, {
      kind: "card_written",
      recommendation_id: card.id,
      type: card.type,
      summary: card.summary,
      lens: card.lens,
      confidence_label: card.confidence.label,
    }, 76 + index * 3),
  ),
  stageDone(400, "write", "Wrote 5 cards", 92),
  stageStart(300, "refresh", "Checking your descriptions"),
  insight(700, "refresh", "Proton therapy video: 1 line to add", { kind: "refresh_suggested", video_id: videos.apolloProton.video_id, title: videos.apolloProton.title, insertions: 1 }, 96),
  stageDone(300, "refresh", "1 refresh, 2 hygiene fixes", 99),
  { delayMs: 500, type: "run.succeeded", status: "succeeded", message: "Your first brief is ready", percent: 100 },
];

export const RUN_SCRIPTS: Partial<Record<RunKind, ScriptedEvent[]>> = { onboarding, analysis };
