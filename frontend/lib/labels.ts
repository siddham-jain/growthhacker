import type { ContextSourceKind, CompetitorRelation, Lens, RecType } from "@/lib/api/types";

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
  aggregator: "Aggregator",
  content: "Content",
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
