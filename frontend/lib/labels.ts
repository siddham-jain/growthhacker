import type { AgeMethod, CompetitorRelation, CompetitorRole, ContextSourceKind, FeedbackReason, Lens, RecType } from "@/lib/api/types";

export const REC_TYPE_LABELS: Record<RecType, string> = {
  video_idea: "Video idea",
  title_pattern: "Title pattern",
  thumbnail_pattern: "Thumbnail pattern",
  format: "Format",
  creator: "Creator",
  news_opportunity: "News",
  description_refresh: "Refresh",
};

export const LENS_LABELS: Record<Lens, string> = { views: "Views", conversion: "Conversion" };

export const RELATION_LABELS: Record<CompetitorRelation, string> = {
  direct: "Direct",
  direct_public: "Direct (public sector)",
  aggregator: "Aggregator",
  content: "Content",
  benchmark: "Benchmark",
  self: "Your own brand",
  subsidiary: "Subsidiary",
  parent: "Parent company",
  partner: "Partner",
  supplier: "Supplier",
  insurer: "Insurer",
  unrelated: "Unrelated",
};

interface SourceKindLabel {
  label: string;
  short: string;
  description: string;
  thirdParty: boolean;
}

export const SOURCE_KIND_LABELS: Record<ContextSourceKind, SourceKindLabel> = {
  site_direct: { label: "Your website", short: "Site", description: "Read straight from your site", thirdParty: false },
  site_rendered: { label: "Browser render", short: "Render", description: "Your site, rendered in a real browser", thirdParty: false },
  reader_proxy: { label: "Reader service", short: "Reader", description: "Public copy fetched by a reader service when your site blocked us", thirdParty: false },
  search_index: { label: "Search index", short: "Index", description: "Your pages as stored by a search index", thirdParty: false },
  archive: { label: "Web archive", short: "Archive", description: "An archived copy; may be out of date", thirdParty: true },
  knowledge_base: { label: "Wikipedia / Wikidata", short: "Wiki", description: "Public knowledge base entry", thirdParty: true },
  web_mentions: { label: "Other websites", short: "Mentions", description: "What other sites say about you", thirdParty: true },
  youtube_channel: { label: "Your YouTube channel", short: "YouTube", description: "Channel description, links and recent videos", thirdParty: false },
  user: { label: "You", short: "You", description: "Added or edited by you", thirdParty: false },
};

export const PROFILE_FIELD_LABELS: Record<string, string> = {
  offerings: "Offerings",
  value_props: "Why customers choose you",
  locations: "Locations",
  conversion_actions: "Ways customers reach you",
  buyer_questions: "Buyer questions",
};

export const ROLE_LABELS: Record<CompetitorRole, string> = { competitor_set: "Competitor", niche_set: "Niche", benchmark_set: "Benchmark", creator_seed: "Creator", excluded: "Excluded" };

interface MethodLabel {
  label: string;
  description: string;
  proxy: boolean;
}

// a proxy is never presented as a measured value; the label travels with the number
export const METHOD_LABELS: Record<AgeMethod, MethodLabel> = {
  observed: { label: "Observed", description: "Measured from our daily snapshots at that age", proxy: false },
  interpolated: { label: "Interpolated", description: "Between two snapshots taken either side of that age", proxy: false },
  owner_analytics: { label: "Owner analytics", description: "Exact value from YouTube Analytics, connected by the channel owner", proxy: false },
  age_band_proxy: { label: "Proxy", description: "Estimated from the video's age band because we started tracking it late", proxy: true },
  pending: { label: "Pending", description: "The video isn't old enough yet", proxy: true },
  unavailable: { label: "Unavailable", description: "No snapshot near that age", proxy: true },
};

export const FEEDBACK_REASON_LABELS: Record<FeedbackReason, string> = {
  not_our_business: "Not our business",
  already_covered: "We already covered this",
  cannot_produce_format: "We can't make this format",
  creator_not_suitable: "Creator isn't a fit",
  evidence_wrong: "The evidence looks wrong",
  other: "Something else",
};

export const METRIC_DISCLOSURE = "Calculated by GrowthHacker from public YouTube data. Hits, ratios, averages and ranges are not YouTube metrics.";
