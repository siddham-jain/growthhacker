import { channels, video } from "./base";

export const videos = {
  fortisBypassCost: video("fx-bypass-cost", channels.fortis, "Bypass surgery cost in India: what's included and what's not", 9, 41_200, 4.8),
  narayanaHeartCost: video("nx-heart-cost", channels.narayana, "How much does heart surgery cost at Narayana Health? A surgeon answers", 14, 28_900, 3.9),
  medantaAngioVsBypass: video("md-angio-bypass", channels.medanta, "Angioplasty or bypass? How doctors decide", 6, 52_300, 3.4),
  maxTransplantJourney: video("mx-transplant-day", channels.max, "Liver transplant, day by day: one family's 30 days", 21, 36_100, 5.2),
  manipalKneeCost: video("mp-knee-cost", channels.manipal, "Knee replacement cost in Bengaluru, explained in 6 minutes", 11, 14_800, 3.1),
  fortisSecondOpinion: video("fx-second-opinion", channels.fortis, "When should you get a second opinion before surgery?", 18, 18_400, 3.6),
  practoSymptoms: video("pr-chest-pain-short", channels.practo, "Chest pain: 3 signs that need the ER now #shorts", 3, 120_400, 3.3, "short_form"),
  heartSmartHindi: video("hs-heart-attack-hi", channels.drHeartTalks, "हार्ट अटैक के 5 शुरुआती लक्षण | Heart attack early signs", 8, 88_000, 4.1),
  doctorExplainsRobotic: video("de-robotic-surgery", channels.doctorExplains, "Robotic surgery in India: is it worth the extra cost?", 12, 210_000, 3.7),
  ashaCheckup: video("ha-health-checkup", channels.healthWithAsha, "Which full-body checkup do you actually need after 40?", 16, 9_400, 3.2),
  apolloProton: video("ap-proton-therapy", channels.apollo, "Proton therapy explained: who it helps and how it works", 19, 6_900, 2.4),
  apolloStroke: video("ap-stroke-signs", channels.apollo, "BE FAST: spotting a stroke in under a minute", 5, 4_100, 1.6),
  apolloTour: video("ap-campus-tour", channels.apollo, "Inside our new Navi Mumbai campus", 24, 1_100, 0.4),
  apolloCheckup: video("ap-prohealth", channels.apollo, "What happens during a ProHealth check, step by step", 28, 2_300, 0.9),
  apolloTransplantStory: video("ap-transplant-story", channels.apollo, "A second chance: a kidney transplant story from Chennai", 12, 3_900, 1.5),
};
