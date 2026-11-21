import type { ChannelRef, KeywordSource, KeywordTrend, TrendIdea, TrendScan, TrendVideo } from "../../types";
import { channels, daysAgo, NOW } from "./base";

const KNEE = "tr_0a1b2c3d4e5f";
const HEART = "tr_1b2c3d4e5f60";
const DIABETES = "tr_2c3d4e5f6071";

function clip(id: string, owner: ChannelRef, title: string, ageDays: number, viewsPerDay: number, competitor = false, durationS = 540): TrendVideo {
  return {
    video_id: id,
    channel_id: owner.channel_id,
    channel_title: owner.title,
    title,
    published_at: daysAgo(ageDays),
    views: Math.round(viewsPerDay * ageDays),
    age_days: ageDays,
    views_per_day: viewsPerDay,
    duration_s: durationS,
    thumbnail_url: null,
    is_competitor: competitor,
  };
}

function trend(
  keyword: string,
  topic: [string, string],
  sources: KeywordSource[],
  heat: KeywordTrend["heat"],
  videos: TrendVideo[],
  extra: Partial<KeywordTrend> = {},
): KeywordTrend {
  const rates = videos.map((item) => item.views_per_day).sort((a, b) => a - b);
  return {
    candidate: { keyword, sources, topic_ref_id: topic[0], topic_label: topic[1], autocomplete_rank: sources.includes("autocomplete") ? 1 : null, web_reason: null, web_urls: [] },
    status: "checked",
    checked_at: NOW,
    window_days: 14,
    recent_videos: videos.length + 9,
    channels: new Set(videos.map((item) => item.channel_id)).size + 4,
    competitor_videos: videos.filter((item) => item.is_competitor).length,
    median_views_per_day: rates[Math.floor(rates.length / 2)] ?? null,
    top_views_per_day: rates.at(-1) ?? null,
    leaders_views_per_day: rates[Math.floor(rates.length / 2)] ?? null,
    momentum: null,
    heat,
    videos,
    ...extra,
  };
}

const robotic = trend(
  "robotic knee replacement",
  [KNEE, "knee replacement"],
  ["web", "autocomplete"],
  "hot",
  [
    clip("rbKnee00001", channels.doctorExplains, "Robotic knee surgery explained in Hindi", 3, 31_900),
    clip("rbKnee00002", channels.fortis, "Our first 500 robotic knee replacements", 4, 12_400, true),
    clip("rbKnee00003", channels.healthWithAsha, "Robot vs surgeon: which knee replacement?", 6, 8_100),
    clip("rbKnee00004", channels.medanta, "Robotic knee replacement recovery timeline", 9, 5_300, true),
  ],
  {
    momentum: 2.4,
    candidate: {
      keyword: "robotic knee replacement",
      sources: ["web", "autocomplete"],
      topic_ref_id: KNEE,
      topic_label: "knee replacement",
      autocomplete_rank: 2,
      web_reason: "Two insurers announced cover for robot-assisted joint surgery this week.",
      web_urls: ["https://news.example.in/robotic-knee-cover"],
    },
  },
);

const heartYoung = trend(
  "heart attack in young people",
  [HEART, "heart attack"],
  ["autocomplete"],
  "hot",
  [
    clip("htYoung0001", channels.drHeartTalks, "Why are 30-year-olds having heart attacks?", 2, 44_500, false, 48),
    clip("htYoung0002", channels.narayana, "Heart attack warning signs under 40", 5, 9_800, true),
    clip("htYoung0003", channels.doctorExplains, "Gym and heart attack: what doctors see", 7, 7_200),
  ],
);

const diabetesDiet = trend(
  "diabetes diet plan indian",
  [DIABETES, "diabetes care"],
  ["autocomplete", "profile"],
  "warm",
  [
    clip("dbDiet00001", channels.healthWithAsha, "7-day Indian diabetes diet chart", 8, 6_300),
    clip("dbDiet00002", channels.practo, "Rice and diabetes: what to eat", 10, 3_900),
    clip("dbDiet00003", channels.max, "Diabetes diet myths", 12, 2_100, true),
  ],
);

const kneeCost = trend("knee replacement cost in india", [KNEE, "knee replacement"], ["autocomplete"], "quiet", [clip("knCost00001", channels.practo, "Knee replacement cost breakdown", 11, 900)], { recent_videos: 4, channels: 3 });

function idea(id: string, source: KeywordTrend, title: string, angle: string, fit: string, format: TrendIdea["format"], refs: string[], buyerQuestion: string | null): TrendIdea {
  const why = [`${source.recent_videos} videos on “${source.candidate.keyword}” in the last ${source.window_days} days from ${source.channels} channels`, "Typical one gets 8.1k views a day; the top one 31.9k"];
  if (source.competitor_videos) why.push(`${source.competitor_videos} of them come from your competitors`);
  if (source.momentum) why.push(`Views a day are ${source.momentum.toFixed(1)}x what we saw at the last check`);
  return { id, keyword: source.candidate.keyword, topic_ref_id: source.candidate.topic_ref_id, title, angle, fit_to_client: fit, format, why, reference_video_ids: refs, buyer_question: buyerQuestion };
}

export function trendScan(): TrendScan {
  return {
    as_of: NOW,
    window_days: 14,
    region: "IN",
    language: "en",
    keywords: [robotic, heartYoung, diabetesDiet, kneeCost],
    ideas: [
      idea(
        "ti_robotic0001",
        robotic,
        "Is robotic knee surgery worth it? An orthopaedic surgeon answers",
        "Walk a patient through who gains from robot-assisted surgery and who does not, using a real pre-surgery consult.",
        "Apollo runs robotic joint replacement across its hospitals, so a surgeon can show the system itself.",
        "long",
        ["rbKnee00001", "rbKnee00003", "rbKnee00002"],
        "Which hospital is best for knee replacement?",
      ),
      idea(
        "ti_heart00001",
        heartYoung,
        "The heart attack sign young people ignore most",
        "A cardiologist names one early sign, why it is missed, and when to get an ECG instead of waiting.",
        "Apollo's cardiology teams see these patients every day and can speak with authority.",
        "short",
        ["htYoung0001", "htYoung0002"],
        null,
      ),
      idea(
        "ti_diabet0001",
        diabetesDiet,
        "What a diabetes plate looks like in an Indian home",
        "A dietitian builds breakfast, lunch and dinner from ordinary Indian dishes, swapping portions rather than foods.",
        "Apollo's dietitians already run diabetes clinics, so the advice links straight to a booking.",
        "long",
        ["dbDiet00001", "dbDiet00002"],
        null,
      ),
    ],
    search_calls: 9,
    youtube_units: 9,
    llm_calls: 2,
    tavily_credits: 2,
    messages: [],
  };
}
