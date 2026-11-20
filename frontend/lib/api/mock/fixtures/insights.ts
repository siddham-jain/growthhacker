import type {
  CreatorView,
  NewsOpportunityView,
  OwnPerformance,
  RecommendationOut,
  RefreshSuggestionView,
  TrendingReport,
  WhiteSpaceNote,
} from "../../types";
import { channels, daysAgo, hoursFromNow, NOW, reference, RUN_ID } from "./base";
import { videos } from "./videos";

const base = { window_days: 30 as const, generated_at: NOW, run_id: RUN_ID, rendered_by: "llm" as const };

export const recommendations: RecommendationOut[] = [
  {
    ...base,
    id: "rec_bypasscosthyd",
    type: "video_idea",
    summary: "Short explainer on bypass surgery costs in Hyderabad, under 8 minutes, with what insurance covers",
    lens: ["views", "conversion"],
    buyer_question: "How much does bypass surgery cost in Hyderabad?",
    why_it_works: [
      "4 of 6 competitor outliers in 30 days are cost explainers",
      "Fortis's cost explainer ran 4.8× its normal at day 7",
      "2 of 5 competitors cover this question; you haven't",
    ],
    reference_videos: [reference(videos.fortisBypassCost, "competitor"), reference(videos.narayanaHeartCost, "competitor"), reference(videos.manipalKneeCost, "competitor")],
    fit_to_client: "Matches your cardiac sciences offering and the 'Book an appointment' action; your how-tos average 1.4× your baseline",
    expected_range_vs_baseline: "1.2×–2.0× your baseline (indicative)",
    confidence: { label: "High", outliers: 6, channels: 4, basis: "standard" },
  },
  {
    ...base,
    id: "rec_angiovsbypass",
    type: "video_idea",
    summary: "Cardiologist answers: angioplasty or bypass, how the decision is made",
    lens: ["conversion"],
    buyer_question: "Do I need angioplasty or bypass surgery?",
    why_it_works: [
      "Medanta's decision explainer ran 3.4× its normal at day 7",
      "Decision-stage questions bring 'Book an appointment' clicks in your descriptions",
    ],
    reference_videos: [reference(videos.medantaAngioVsBypass, "competitor"), reference(videos.heartSmartHindi, "niche"), reference(videos.fortisSecondOpinion, "competitor")],
    fit_to_client: "Ties to cardiac sciences; you covered angioplasty once in 90 days",
    expected_range_vs_baseline: "1.1×–1.8× your baseline (indicative)",
    confidence: { label: "Medium", outliers: 3, channels: 3, basis: "standard", caps_applied: ["Capped at Medium: only 3 outliers"] },
  },
  {
    ...base,
    id: "rec_questiontitles",
    type: "title_pattern",
    summary: "Phrase titles as the buyer's question with the city: 'How much does … cost in Hyderabad?'",
    lens: ["views"],
    buyer_question: null,
    why_it_works: ["Question titles: 9 of 12 niche outliers vs 21 of 80 videos (2.1× lift)", "You used this in 1 of 14 videos"],
    reference_videos: [reference(videos.narayanaHeartCost, "competitor"), reference(videos.fortisSecondOpinion, "competitor"), reference(videos.ashaCheckup, "niche")],
    fit_to_client: "Works for every cost and recovery question you track",
    expected_range_vs_baseline: null,
    confidence: { label: "Medium", outliers: 9, channels: 4, basis: "standard" },
  },
  {
    ...base,
    id: "rec_transplantdiary",
    type: "format",
    summary: "Day-by-day patient journey (8–12 minutes) for liver transplant",
    lens: ["views", "conversion"],
    buyer_question: "How long is the wait for a liver transplant in India?",
    why_it_works: ["Max's 30-day transplant diary ran 5.2× its normal at day 7", "Journey formats hold 3 of 5 transplant outliers in the niche"],
    reference_videos: [reference(videos.maxTransplantJourney, "competitor"), reference(videos.apolloTransplantStory, "own"), reference(videos.doctorExplainsRobotic, "niche")],
    fit_to_client: "Your own transplant story ran 1.5× your baseline",
    expected_range_vs_baseline: "1.3×–2.4× your baseline (indicative)",
    confidence: { label: "Low", outliers: 3, channels: 2, basis: "standard", caps_applied: ["Capped at Low: one channel supplies most references"] },
  },
  {
    ...base,
    id: "rec_shortsymptoms",
    type: "video_idea",
    summary: "60-second Short: three chest-pain signs that need the ER, ending on the 1066 helpline",
    lens: ["views"],
    buyer_question: null,
    why_it_works: ["Practo's chest-pain Short ran 3.3× its normal at day 7", "Symptom Shorts are 4 of 7 niche Short outliers"],
    reference_videos: [reference(videos.practoSymptoms, "niche"), reference(videos.heartSmartHindi, "niche"), reference(videos.apolloStroke, "own")],
    fit_to_client: "Points to your emergency line; your stroke Short ran 1.6× your baseline",
    expected_range_vs_baseline: "1.0×–1.6× your baseline (indicative)",
    confidence: { label: "Medium", outliers: 4, channels: 3, basis: "standard" },
  },
];

export const whiteSpace: WhiteSpaceNote[] = [
  { buyer_question: "Is proton therapy right for my cancer?", videos_checked: 412, channels_checked: 14, window_days: 90 },
  { buyer_question: "Does insurance cover robotic surgery?", videos_checked: 412, channels_checked: 14, window_days: 90 },
];

export const newsOpportunities: NewsOpportunityView[] = [
  {
    id: "news_cghs_rates",
    news: { news_id: "ns_cghs", headline: "Revised CGHS package rates for cardiac procedures announced", url: "https://example.org/cghs-rates", source_domain: "pib.gov.in", published_at: daysAgo(0.6) },
    recommendation_id: null,
    summary: "Explainer: what the new CGHS cardiac package rates mean for bypass and angioplasty patients",
    relevance: 0.88,
    offering_refs: ["Cardiac sciences"],
    demand_check: { youtube_results_7d: 6, outliers: 3 },
    status: "new",
    urgency: "now",
    expires_at: hoursFromNow(40),
    reference_count: 3,
    review_required: true,
  },
  {
    id: "news_proton_approval",
    news: { news_id: "ns_proton", headline: "New proton therapy indication approved for paediatric brain tumours", url: "https://example.org/proton", source_domain: "cdsco.gov.in", published_at: daysAgo(1.2) },
    recommendation_id: null,
    summary: "Short: which children's brain tumours can now be treated with proton therapy",
    relevance: 0.81,
    offering_refs: ["Proton therapy"],
    demand_check: { youtube_results_7d: 1, outliers: 0 },
    status: "new",
    urgency: "this_week",
    expires_at: hoursFromNow(110),
    reference_count: 0,
    review_required: true,
  },
  {
    id: "news_dengue",
    news: { news_id: "ns_dengue", headline: "Dengue cases rise across Hyderabad and Chennai after late rains", url: "https://example.org/dengue", source_domain: "thehindu.com", published_at: daysAgo(0.3) },
    recommendation_id: null,
    summary: "Dengue warning signs and when to go to hospital, by an Apollo physician",
    relevance: 0.64,
    offering_refs: ["Emergency care"],
    demand_check: { youtube_results_7d: 2, outliers: 1 },
    status: "new",
    urgency: "now",
    expires_at: hoursFromNow(20),
    reference_count: 2,
    review_required: true,
  },
];

export const refreshSuggestions: RefreshSuggestionView[] = [
  {
    id: "rf_proton",
    video: { video_id: videos.apolloProton.video_id, title: videos.apolloProton.title, published_at: videos.apolloProton.published_at, url: "https://www.youtube.com/watch?v=ap-proton-therapy", views: 18_200 },
    warranted_because: ["'Proton therapy for children' appears in 3 niche outliers this month; your video doesn't mention it."],
    operations: [
      { op_id: "op1", kind: "insert", position: "after_first_paragraph", anchor_excerpt: "Proton therapy targets tumours with high precision…", text: "Now also used for some children's brain tumours. Ask our team whether it fits your case.", keywords_added: ["children's brain tumours"] },
    ],
    inserted_bytes: 92,
    resulting_bytes: 1_486,
    expected_effect: "Helps this video show up for searches about proton therapy for children. No view promise.",
    basis: "keyword",
    review_required: true,
    status: "open",
    applied_at: null,
  },
  {
    id: "rf_stroke_cta",
    video: { video_id: videos.apolloStroke.video_id, title: videos.apolloStroke.title, published_at: videos.apolloStroke.published_at, url: "https://www.youtube.com/watch?v=ap-stroke-signs", views: 9_800 },
    warranted_because: ["Description has no way to reach you: no booking link or helpline."],
    operations: [
      { op_id: "op2", kind: "insert", position: "top_line", anchor_excerpt: "BE FAST is the easiest way to remember…", text: "Emergency? Call 1066 for an Apollo ambulance.", keywords_added: [] },
    ],
    inserted_bytes: 47,
    resulting_bytes: 802,
    expected_effect: "Gives viewers a direct next step. No view promise.",
    basis: "rule_based",
    review_required: false,
    status: "open",
    applied_at: null,
  },
  {
    id: "rf_campus_link",
    video: { video_id: videos.apolloTour.video_id, title: videos.apolloTour.title, published_at: videos.apolloTour.published_at, url: "https://www.youtube.com/watch?v=ap-campus-tour", views: 3_100 },
    warranted_because: ["A link in the description returns 404."],
    operations: [
      { op_id: "op3", kind: "link_fix", position: "before_links_block", anchor_excerpt: "Visit us:", old_text: "https://www.apollohospitals.com/navimumbai-old", text: "https://www.apollohospitals.com/locations/navi-mumbai", keywords_added: [] },
    ],
    inserted_bytes: 6,
    resulting_bytes: 640,
    expected_effect: "Fixes a broken link. No view promise.",
    basis: "rule_based",
    review_required: false,
    status: "open",
    applied_at: null,
  },
];

export const creators: CreatorView[] = [
  {
    channel: channels.drHeartTalks,
    rank: 1,
    subscriber_band: "100K–1M subscribers",
    cadence_label: "posts every 4 days",
    relevance: { matched_videos: 9, recent_videos: 14, topics: ["heart attack signs", "bypass surgery"] },
    recent_performance_vs_niche: "median 1.8× their own normal",
    consistency: "posted 13 times in 90 days · 46% of videos beat their normal",
    collaborations: [{ video_id: "hs-collab-1", competitor_name: "Fortis Healthcare", label: "mention", date: daysAgo(39) }],
    reference_videos: [reference(videos.heartSmartHindi, "creator")],
    recommendation_id: null,
  },
  {
    channel: channels.healthWithAsha,
    rank: 2,
    subscriber_band: "10K–100K subscribers",
    cadence_label: "posts weekly",
    relevance: { matched_videos: 6, recent_videos: 11, topics: ["health checkups", "women's health"] },
    recent_performance_vs_niche: "median 1.3× their own normal",
    consistency: "posted 11 times in 90 days · 36% of videos beat their normal",
    collaborations: [{ video_id: "ha-collab-1", competitor_name: "Max Healthcare", label: "disclosed_paid_promotion", date: daysAgo(79) }],
    reference_videos: [reference(videos.ashaCheckup, "creator")],
    recommendation_id: null,
  },
];

export const ownPerformance: OwnPerformance = {
  youtube_public: {
    median_ratio_window: 1.3,
    median_ratio_prev_window: 1.1,
    comparison_age_days: 7,
    videos_in_window: 9,
    best: [videos.apolloProton, videos.apolloStroke, videos.apolloTransplantStory],
    worst: [videos.apolloTour, videos.apolloCheckup],
  },
  youtube_analytics: null,
  website: null,
};

export const trending: TrendingReport = {
  items: [
    { ...videos.practoSymptoms, velocity_per_hour: 1_840 },
    { ...videos.doctorExplainsRobotic, velocity_per_hour: 1_120 },
    { ...videos.medantaAngioVsBypass, velocity_per_hour: 640 },
  ],
  rising_topics: [
    { label: "Cost explainers", uploads_prev: 3, uploads_now: 7, outliers_prev: 1, outliers_now: 4 },
    { label: "Second opinions", uploads_prev: 1, uploads_now: 4, outliers_prev: 0, outliers_now: 2 },
  ],
};

export const outliers = [
  videos.maxTransplantJourney,
  videos.fortisBypassCost,
  videos.heartSmartHindi,
  videos.narayanaHeartCost,
  videos.doctorExplainsRobotic,
  videos.fortisSecondOpinion,
  videos.medantaAngioVsBypass,
];
