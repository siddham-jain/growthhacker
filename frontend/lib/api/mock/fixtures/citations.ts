import type { AnswerCitations, ChannelRef, CitedVideo, QuestionCitations } from "../../types";
import { channels, daysAgo } from "./base";

function cited(videoId: string, title: string, channel: ChannelRef, owner: CitedVideo["owner"], position: number | null): CitedVideo {
  return {
    owner,
    position,
    cited_in: position === null ? "answer" : "result",
    video: {
      video_id: videoId,
      channel_id: channel.channel_id,
      title,
      channel_title: channel.title,
      published_at: daysAgo(120),
      url: `https://www.youtube.com/watch?v=${videoId}`,
      thumbnail_url: `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg`,
    },
  };
}

function question(id: string, text: string, status: QuestionCitations["status"], videos: CitedVideo[], youtubeUrls = videos.length): QuestionCitations {
  return { buyer_question_id: id, text, funnel_stage: "consideration", checked_at: daysAgo(2), status, youtube_urls: youtubeUrls, videos };
}

const questions: QuestionCitations[] = [
  question("bq_bypass_cost", "How much does bypass surgery cost in Hyderabad?", "cites_you", [
    cited("apBypassCo1", "Bypass surgery cost explained | Apollo Hospitals", channels.apollo, "own", 2),
    cited("ftBypassCo1", "What does heart bypass cost? Fortis explains", channels.fortis, "competitor", 4),
  ]),
  question("bq_angio_or_bypass", "Do I need angioplasty or bypass surgery?", "competitor_only", [
    cited("nhAngioBy01", "Angioplasty vs bypass: which one do you need?", channels.narayana, "competitor", 1),
    cited("mdAngioBy01", "Stent or bypass? A cardiac surgeon answers", channels.medanta, "competitor", null),
  ]),
  question("bq_knee_recovery", "How long does recovery take after knee replacement?", "competitor_only", [
    cited("ftKneeRec01", "Knee replacement recovery week by week", channels.fortis, "competitor", 3),
  ]),
  question("bq_second_opinion", "Should I get a second opinion before heart surgery?", "others_only", [
    cited("dhSecondOp1", "Why a second opinion matters before surgery", channels.drHeartTalks, "other", 5),
  ]),
  question("bq_insurance", "Does insurance cover robotic surgery?", "no_youtube", [], 0),
];

export function answerCitations(): AnswerCitations {
  return {
    lens: "conversion",
    signal: "cited_in_ai_answers",
    source_label: "Web answer search (Tavily), videos resolved with the YouTube Data API",
    disclosure: "Calculated by GrowthHacker, not a YouTube metric",
    window_days: 28,
    own_channel_id: channels.apollo.channel_id,
    last_checked_at: daysAgo(2),
    counts: { questions_checked: 5, cite_you: 1, cite_competitors: 3, competitor_only: 2, no_tracked_channel: 2 },
    questions,
    competitors: [
      { channel_id: channels.fortis.channel_id, channel_title: channels.fortis.title, buyer_question_ids: ["bq_bypass_cost", "bq_knee_recovery"] },
      { channel_id: channels.medanta.channel_id, channel_title: channels.medanta.title, buyer_question_ids: ["bq_angio_or_bypass"] },
      { channel_id: channels.narayana.channel_id, channel_title: channels.narayana.title, buyer_question_ids: ["bq_angio_or_bypass"] },
    ],
  };
}
