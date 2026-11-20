import type { BuyerQuestionItem, KeywordItem } from "../../types";
import { daysAgo } from "./base";

function keyword(id: string, text: string, status: KeywordItem["status"], priority: number): KeywordItem {
  return { id, text, status, source: "profile", priority, created_at: daysAgo(0) };
}

function question(
  id: string,
  text: string,
  stage: BuyerQuestionItem["intent_stage"],
  status: BuyerQuestionItem["status"] = "suggested",
): BuyerQuestionItem {
  return { id, text, status, source: "profile", intent_stage: stage, cited_video_ids: [], priority: 3, created_at: daysAgo(0) };
}

export const keywords: KeywordItem[] = [
  keyword("kw_bypass", "bypass surgery", "suggested", 1),
  keyword("kw_angioplasty", "angioplasty", "suggested", 2),
  keyword("kw_proton", "proton therapy", "suggested", 1),
  keyword("kw_liver_transplant", "liver transplant", "suggested", 2),
  keyword("kw_knee", "knee replacement", "suggested", 2),
  keyword("kw_robotic", "robotic surgery", "suggested", 3),
  keyword("kw_health_check", "full body health checkup", "suggested", 3),
  keyword("kw_second_opinion", "second opinion", "suggested", 3),
];

export const buyerQuestions: BuyerQuestionItem[] = [
  question("bq_bypass_cost", "How much does bypass surgery cost in Hyderabad?", "decision"),
  question("bq_angio_or_bypass", "Do I need angioplasty or bypass surgery?", "consideration"),
  question("bq_proton_fit", "Is proton therapy right for my cancer?", "consideration"),
  question("bq_transplant_wait", "How long is the wait for a liver transplant in India?", "consideration"),
  question("bq_knee_recovery", "How long does recovery take after knee replacement?", "decision"),
  question("bq_checkup_after_40", "Which health checkup should I get after 40?", "awareness"),
  question("bq_second_opinion", "Should I get a second opinion before heart surgery?", "consideration"),
  question("bq_insurance", "Does insurance cover robotic surgery?", "decision"),
];
