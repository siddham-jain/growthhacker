import type { BuyerQuestionItem, KeywordItem } from "../../types";
import { daysAgo } from "./base";

function keyword(id: string, text: string, weight: number): KeywordItem {
  return { id, text, status: "proposed", source: "profile", weight, kind: "service", language: "en", updated_at: daysAgo(0) };
}

function question(id: string, text: string, stage: BuyerQuestionItem["funnel_stage"], priority = 2): BuyerQuestionItem {
  return { id, text, status: "proposed", source: "website_derived", funnel_stage: stage, intent: "cost", cited_video_ids: [], priority, created_at: daysAgo(0) };
}

export const keywords: KeywordItem[] = [
  keyword("kw_bypass", "bypass surgery", 1),
  keyword("kw_angioplasty", "angioplasty", 0.8),
  keyword("kw_proton", "proton therapy", 1),
  keyword("kw_liver_transplant", "liver transplant", 0.8),
  keyword("kw_knee", "knee replacement", 0.8),
  keyword("kw_robotic", "robotic surgery", 0.6),
  keyword("kw_health_check", "full body health checkup", 0.6),
  keyword("kw_second_opinion", "second opinion", 0.6),
];

export const buyerQuestions: BuyerQuestionItem[] = [
  question("bq_bypass_cost", "How much does bypass surgery cost in Hyderabad?", "decision", 1),
  question("bq_angio_or_bypass", "Do I need angioplasty or bypass surgery?", "consideration", 1),
  question("bq_proton_fit", "Is proton therapy right for my cancer?", "consideration"),
  question("bq_transplant_wait", "How long is the wait for a liver transplant in India?", "consideration"),
  question("bq_knee_recovery", "How long does recovery take after knee replacement?", "decision"),
  question("bq_checkup_after_40", "Which health checkup should I get after 40?", "awareness", 3),
  question("bq_second_opinion", "Should I get a second opinion before heart surgery?", "consideration"),
  question("bq_insurance", "Does insurance cover robotic surgery?", "decision"),
];
