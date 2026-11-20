import type {
  AgeMethod,
  CreatorView,
  EarlySignalView,
  InsertionOp,
  NewsOpportunityView,
  OwnPerformance,
  RecommendationOut,
  RefreshSuggestionView,
  TrendingReport,
  WhiteSpaceNote,
} from "../../types";
import { channels, curve, daysAgo, hoursFromNow, NOW, reference, RUN_ID, unifiedDiff } from "./base";
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
  { buyer_question_id: "bq_proton_fit", text: "Is proton therapy right for my cancer?", videos_checked: 412, channels_checked: 14, window_days: 90, widest_window_checked: 90 },
  { buyer_question_id: "bq_insurance", text: "Does insurance cover robotic surgery?", videos_checked: 412, channels_checked: 14, window_days: 90, widest_window_checked: 90 },
];

export const newsOpportunities: NewsOpportunityView[] = [
  {
    id: "news_cghs_rates",
    news: { news_id: "ns_cghs", headline: "Revised CGHS package rates for cardiac procedures announced", url: "https://example.org/cghs-rates", source_domain: "pib.gov.in", published_at: daysAgo(0.6) },
    recommendation_id: null,
    summary: "Explainer: what the new CGHS cardiac package rates mean for bypass and angioplasty patients",
    relevance: 0.88,
    offering_refs: ["Cardiac sciences"],
    demand_check: { youtube_results_7d: 6, outliers: 3, status: "checked" },
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
    demand_check: { youtube_results_7d: 0, outliers: 0, status: "checked" },
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
    demand_check: { youtube_results_7d: 2, outliers: 1, status: "checked" },
    status: "new",
    urgency: "now",
    expires_at: hoursFromNow(20),
    reference_count: 2,
    review_required: true,
  },
];

const PROTON_DESCRIPTION = [
  "Proton therapy targets tumours with high precision, sparing healthy tissue around them. Dr. Srinivas explains who it helps and how a course of treatment works.",
  "",
  "00:00 What proton therapy is",
  "01:42 Which cancers it treats",
  "04:10 What a treatment day looks like",
  "06:55 Costs and insurance",
  "",
  "Book a consultation: https://www.apollohospitals.com/proton-cancer-centre",
  "Call 1860-500-1066",
  "",
  "#ProtonTherapy #CancerCare #ApolloHospitals",
].join("\n");

const STROKE_DESCRIPTION = [
  "BE FAST is the easiest way to remember the signs of a stroke: Balance, Eyes, Face, Arms, Speech, Time.",
  "Every minute counts. Our neurologists explain what to do while you wait for help.",
  "",
  "#Stroke #BEFAST #ApolloHospitals",
].join("\n");

const CAMPUS_DESCRIPTION = [
  "Take a walk through our new Navi Mumbai campus: the emergency wing, the cardiac ICU and the day-care suites.",
  "",
  "Visit us:",
  "https://www.apollohospitals.com/navimumbai-old",
  "",
  "#NaviMumbai #ApolloHospitals",
].join("\n");

function op(id: string, kind: InsertionOp["kind"], position: InsertionOp["position"], anchor: string, text: string, keywords: string[] = [], oldText: string | null = null): InsertionOp {
  return {
    op_id: id,
    kind,
    position,
    anchor: { anchor_excerpt: anchor, anchor_hash: id, strategy: "line" },
    claim_level: "L1_context",
    phrased_by: "template",
    text,
    text_bytes: new TextEncoder().encode(text).length,
    keywords_added: keywords,
    hashtags_added: [],
    trigger_ids: [],
    old_text: oldText,
  };
}

const PROTON_AFTER = PROTON_DESCRIPTION.replace("works.\n\n", "works.\nNow also used for some children's brain tumours. Ask our team whether it fits your case.\n\n");
const STROKE_AFTER = `Emergency? Call 1066 for an Apollo ambulance.\n${STROKE_DESCRIPTION}`;
const CAMPUS_AFTER = CAMPUS_DESCRIPTION.replace("https://www.apollohospitals.com/navimumbai-old", "https://www.apollohospitals.com/locations/navi-mumbai");

const refreshBase = { advisories: [], expires_at: hoursFromNow(24 * 20), description_changed: false };

export const refreshSuggestions: RefreshSuggestionView[] = [
  {
    id: "rf_proton",
    video: { video_id: videos.apolloProton.video_id, title: videos.apolloProton.title, published_at: videos.apolloProton.published_at, url: "https://www.youtube.com/watch?v=ap-proton-therapy" },
    warranted_because: ["'Proton therapy for children' appears in 3 niche outliers this month; your video doesn't mention it."],
    operations: [
      op("op1", "insert", "after_first_paragraph", "Proton therapy targets tumours with high precision…", "Now also used for some children's brain tumours. Ask our team whether it fits your case.", ["children's brain tumours"]),
    ],
    inserted_bytes: 92,
    resulting_bytes: 1_486,
    ...refreshBase,
    original_description_hash: "h_proton",
    preview_diff: unifiedDiff(PROTON_DESCRIPTION, PROTON_AFTER),
    expected_effect: "Helps this video show up for searches about proton therapy for children. No view promise.",
    basis: "keyword",
    review_required: true,
    status: "open",
    applied_at: null,
  },
  {
    id: "rf_stroke_cta",
    video: { video_id: videos.apolloStroke.video_id, title: videos.apolloStroke.title, published_at: videos.apolloStroke.published_at, url: "https://www.youtube.com/watch?v=ap-stroke-signs" },
    warranted_because: ["Description has no way to reach you: no booking link or helpline."],
    operations: [
      op("op2", "insert", "top_line", "BE FAST is the easiest way to remember…", "Emergency? Call 1066 for an Apollo ambulance."),
    ],
    inserted_bytes: 47,
    resulting_bytes: 802,
    ...refreshBase,
    original_description_hash: "h_stroke",
    preview_diff: unifiedDiff(STROKE_DESCRIPTION, STROKE_AFTER),
    expected_effect: "Gives viewers a direct next step. No view promise.",
    basis: "rule_based",
    review_required: false,
    status: "open",
    applied_at: null,
  },
  {
    id: "rf_campus_link",
    video: { video_id: videos.apolloTour.video_id, title: videos.apolloTour.title, published_at: videos.apolloTour.published_at, url: "https://www.youtube.com/watch?v=ap-campus-tour" },
    warranted_because: ["A link in the description returns 404."],
    operations: [
      op("op3", "link_fix", "before_links_block", "Visit us:", "https://www.apollohospitals.com/locations/navi-mumbai", [], "https://www.apollohospitals.com/navimumbai-old"),
    ],
    inserted_bytes: 6,
    resulting_bytes: 640,
    ...refreshBase,
    original_description_hash: "h_campus",
    preview_diff: unifiedDiff(CAMPUS_DESCRIPTION, CAMPUS_AFTER),
    expected_effect: "Fixes a broken link. No view promise.",
    basis: "rule_based",
    review_required: false,
    status: "open",
    applied_at: null,
  },
];

function creator(
  channel: CreatorView["channel"],
  group: CreatorView["group"],
  position: number,
  matchShare: number,
  topics: number,
  uploads: number,
  medianRatio: number,
  summary: string,
  refs: CreatorView["reference_videos"],
  collaborations: CreatorView["collaborations"] = [],
): CreatorView {
  return {
    channel,
    group,
    position,
    match_share: matchShare,
    topics_covered: topics,
    uploads_90d: uploads,
    subscriber_band: (channel.subscriber_count ?? 0) >= 100_000 ? "100K–1M subscribers" : "10K–100K subscribers",
    summary,
    performance: { comparison_age_days: 7, median_outlier_ratio: medianRatio, median_views_at_age: null, niche_median_views_at_age: 12_000 },
    consistency: { gap_cv: 0.4, hit_rate: 0.4, last_upload_at: daysAgo(3), median_gap_days: Math.round(90 / uploads) },
    collaborations,
    reference_videos: refs,
    recommendation_id: `rec_creator_${position}`,
    sort: "match_share",
    sort_value: matchShare,
  };
}

const cardioDoc = { ...channels.doctorExplains, channel_id: "UCcardiodoc000000001", title: "Cardio Doc Ravi", handle: "@CardioDocRavi", subscriber_count: 310_000 };
const jointSmart = { ...channels.healthWithAsha, channel_id: "UCjointsmart00000001", title: "Joint Smart", handle: "@JointSmart", subscriber_count: 48_000 };

export const creators: CreatorView[] = [
  creator(channels.drHeartTalks, "strong", 1, 0.64, 2, 13, 1.8, "Heart attack signs and bypass explainers, often in Hindi.", [reference(videos.heartSmartHindi, "creator")], [
    { video_id: "hs-collab-1", competitor_channel_id: channels.fortis.channel_id, competitor_name: "Fortis Healthcare", kind: "mentions_competitor", matched_on: "handle", matched_token: "@FortisHealthcare", published_at: daysAgo(39) },
  ]),
  creator(channels.healthWithAsha, "strong", 2, 0.55, 2, 11, 1.3, "Health checkups and women's health for a general audience.", [reference(videos.ashaCheckup, "creator")], [
    { video_id: "ha-collab-1", competitor_channel_id: channels.max.channel_id, competitor_name: "Max Healthcare", kind: "disclosed_paid_promotion", matched_on: "brand_name", matched_token: "Max Healthcare", published_at: daysAgo(79) },
  ]),
  creator(cardioDoc, "related", 3, 0.39, 2, 24, 1.4, "A cardiologist answering bypass and angioplasty questions.", [reference(videos.medantaAngioVsBypass, "creator")]),
  creator(jointSmart, "related", 4, 0.44, 1, 9, 1.1, "Knee replacement recovery stories.", [reference(videos.manipalKneeCost, "creator")]),
];

export const earlySignals: EarlySignalView[] = [
  {
    id: "es_proton",
    opportunity_id: "news_proton_approval",
    key_entity: "Proton therapy for paediatric brain tumours",
    angle: "Short: which children's brain tumours can now be treated with proton therapy",
    urgency: "this_week",
    expires_at: hoursFromNow(110),
    window_days: 7,
    confidence: { label: "Low", outliers: 0, channels: 0, basis: "first_mover", caps_applied: [], matching_videos: 0, rule_id: "news_v1" },
    reference_videos: [],
    sources: [{ publisher: "cdsco.gov.in", url: "https://example.org/proton", title: "New proton therapy indication approved for paediatric brain tumours", published_at: daysAgo(1.2), tier: "tier1_primary" }],
    statements: ["Approval covers two tumour types in children under 14."],
  },
  {
    id: "es_dengue",
    opportunity_id: "news_dengue",
    key_entity: "Dengue in Hyderabad and Chennai",
    angle: "Dengue warning signs and when to go to hospital, by an Apollo physician",
    urgency: "now",
    expires_at: hoursFromNow(20),
    window_days: 7,
    confidence: { label: "Low", outliers: 1, channels: 1, basis: "first_mover", caps_applied: [], matching_videos: 0, rule_id: "news_v1" },
    reference_videos: [reference(videos.practoSymptoms, "niche")],
    sources: [{ publisher: "thehindu.com", url: "https://example.org/dengue", title: "Dengue cases rise across Hyderabad and Chennai after late rains", published_at: daysAgo(0.3), tier: "tier2_established" }],
    statements: [],
  },
];

const ownVideos = [videos.apolloProton, videos.apolloStroke, videos.apolloTour, videos.apolloCheckup, videos.apolloTransplantStory, videos.apolloKidneyDiet, videos.apolloKnee, videos.apolloDiabetes, videos.apolloAsk];


export const ownPerformance: OwnPerformance = {
  youtube_public: {
    median_ratio_window: 1.3,
    median_ratio_prev_window: 1.1,
    comparison_age_days: 7,
    videos_in_window: ownVideos.length,
    method_mix: ownVideos.reduce<Partial<Record<AgeMethod, number>>>((mix, item) => ({ ...mix, [item.views_at_age.method]: (mix[item.views_at_age.method] ?? 0) + 1 }), {}),
    videos: ownVideos
      .map((item) => ({ video_id: item.video_id, title: item.title, published_at: item.published_at, ratio: item.outlier_ratio, method: item.views_at_age.method, segment: item.segment }))
      .sort((a, b) => Date.parse(a.published_at) - Date.parse(b.published_at)),
    baseline: { age_days: 7, views: 2_900, method: "observed", n: 24 },
    curves: [videos.apolloProton, videos.apolloStroke, videos.apolloTransplantStory].map(curve),
    best: [videos.apolloProton, videos.apolloStroke, videos.apolloTransplantStory],
    worst: [videos.apolloTour, videos.apolloCheckup],
  },
  youtube_analytics: null,
  website: null,
};

export const trending: TrendingReport = {
  items: [
    { ...videos.practoSymptoms, views_per_day: 44_160 },
    { ...videos.doctorExplainsRobotic, views_per_day: 26_880 },
    { ...videos.medantaAngioVsBypass, views_per_day: 15_360 },
  ],
  rising_topics: [
    { topic_ref_id: "kw_bypass", label: "Cost explainers", uploads_prev: 3, uploads_now: 7, outliers_prev: 1, outliers_now: 4 },
    { topic_ref_id: "kw_second_opinion", label: "Second opinions", uploads_prev: 1, uploads_now: 4, outliers_prev: 0, outliers_now: 2 },
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
