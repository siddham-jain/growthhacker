import type { BusinessProfile, BusinessProfileView, ClaimMeta, ContextSourceKind, ProfileFact } from "../../types";
import { daysAgo } from "./base";

const SITE = "https://www.apollohospitals.com";

function meta(sourceKind: ContextSourceKind, url: string, quote: string, status: ClaimMeta["status"] = "proposed"): ClaimMeta {
  return {
    origin: sourceKind === "user" ? "user" : "extracted",
    status,
    confidence: sourceKind === "web_mentions" ? 0.55 : 0.86,
    evidence: [
      {
        url,
        quote,
        source_kind: sourceKind,
        fetched_at: daysAgo(0),
        snapshot_at: sourceKind === "archive" ? "2025-09-14T00:00:00Z" : null,
      },
    ],
  };
}

function fact(id: string, label: string, sourceKind: ContextSourceKind, url: string, quote: string, detail?: string): ProfileFact {
  const thirdParty = sourceKind === "archive" || sourceKind === "knowledge_base" || sourceKind === "web_mentions";
  return { id, label, detail, meta: meta(sourceKind, url, quote, thirdParty ? "needs_confirmation" : "proposed") };
}

const profile: BusinessProfile = {
  identity: {
    name: "Apollo Hospitals",
    one_liner: "Multi-specialty hospital network across India for cardiac, cancer, transplant and preventive care.",
    industry: "healthcare",
    niche: "multi-specialty hospitals",
    country: "IN",
    timezone: "Asia/Kolkata",
    website_domains: ["apollohospitals.com"],
    brand_aliases: ["Apollo", "Apollo Hospitals Enterprise"],
    one_liner_meta: meta("knowledge_base", "https://en.wikipedia.org/wiki/Apollo_Hospitals", "Apollo Hospitals Enterprise Limited is an Indian multinational healthcare group"),
  },
  offerings: [
    fact("off_cardiac", "Cardiac sciences", "reader_proxy", `${SITE}/departments/cardiology`, "Comprehensive cardiac care including bypass surgery, angioplasty and valve repair", "Bypass, angioplasty, valve repair"),
    fact("off_oncology", "Cancer care", "reader_proxy", `${SITE}/departments/oncology`, "Apollo Cancer Centres offer surgical, medical and radiation oncology"),
    fact("off_proton", "Proton therapy", "search_index", `${SITE}/proton-cancer-centre`, "South Asia's first proton therapy centre in Chennai"),
    fact("off_transplant", "Organ transplant", "reader_proxy", `${SITE}/departments/transplant`, "Liver, kidney, heart and lung transplant programmes"),
    fact("off_ortho", "Orthopaedics and joint replacement", "search_index", `${SITE}/departments/orthopaedics`, "Knee and hip replacement including robotic-assisted surgery"),
    fact("off_robotic", "Robotic surgery", "reader_proxy", `${SITE}/robotic-surgery`, "Robotic-assisted procedures across urology, gynaecology and oncology"),
    fact("off_prohealth", "Preventive health checks (ProHealth)", "youtube_channel", "https://www.youtube.com/@ApolloHospitalsIndia/about", "Book your Apollo ProHealth check today"),
  ],
  value_propositions: [
    fact("vp_scale", "Large multi-city network with JCI-accredited hospitals", "knowledge_base", "https://www.wikidata.org/wiki/Q4779884", "Apollo Hospitals operates hospitals across India"),
    fact("vp_firsts", "Early adopter of proton and robotic surgery in India", "reader_proxy", `${SITE}/about-us`, "first proton therapy centre in South Asia"),
    fact("vp_reviews", "Patients cite short waiting times for consults", "web_mentions", "https://www.practo.com/apollo-hospitals/reviews", "appointment within the week, doctors explained everything"),
  ],
  locations: [
    fact("loc_chennai", "Chennai (Greams Road)", "reader_proxy", `${SITE}/locations/chennai`, "Apollo Hospitals, Greams Road, Chennai", "Chennai, Tamil Nadu"),
    fact("loc_hyderabad", "Hyderabad (Jubilee Hills)", "reader_proxy", `${SITE}/locations/hyderabad`, "Apollo Health City, Jubilee Hills"),
    fact("loc_delhi", "New Delhi (Indraprastha)", "search_index", `${SITE}/locations/delhi`, "Indraprastha Apollo Hospitals, Sarita Vihar"),
    fact("loc_bengaluru", "Bengaluru", "reader_proxy", `${SITE}/locations/bangalore`, "Apollo Hospitals, Bannerghatta Road"),
    fact("loc_navi_mumbai", "Navi Mumbai", "archive", `${SITE}/locations/navi-mumbai`, "Apollo Hospitals, CBD Belapur, Navi Mumbai"),
  ],
  conversion_actions: [
    fact("ca_book", "Book an appointment", "reader_proxy", `${SITE}/book-appointment`, "Book Appointment"),
    fact("ca_call", "Call 1860-500-1066", "reader_proxy", `${SITE}/contact-us`, "Call 1860 500 1066"),
    fact("ca_checkup", "Book a health check", "youtube_channel", "https://www.youtube.com/@ApolloHospitalsIndia/about", "Book your Apollo ProHealth check"),
    fact("ca_second_opinion", "Request a second opinion", "search_index", `${SITE}/second-opinion`, "Get an expert second opinion"),
  ],
  freshness: {
    sensitivity: "medium",
    regulated_domain: true,
    domain_kind: "healthcare",
    rationale: "Treatments and approvals change a few times a year; health claims need review before publishing.",
  },
};

export const profileView: BusinessProfileView = {
  profile,
  overrides: {},
  effective: profile,
  version: 1,
  completeness: 0.78,
  partial: true,
  sources: [
    { url: SITE, fetched_at: daysAgo(0), source_kind: "site_direct", claims: 0 },
    { url: SITE, fetched_at: daysAgo(0), source_kind: "site_rendered", claims: 0 },
    { url: `https://r.jina.ai/${SITE}`, fetched_at: daysAgo(0), source_kind: "reader_proxy", claims: 14 },
    { url: SITE, fetched_at: daysAgo(0), source_kind: "search_index", claims: 6 },
    { url: "https://web.archive.org/web/2025/https://www.apollohospitals.com/locations", fetched_at: daysAgo(0), source_kind: "archive", snapshot_at: "2025-09-14T00:00:00Z", claims: 1 },
    { url: "https://en.wikipedia.org/wiki/Apollo_Hospitals", fetched_at: daysAgo(0), source_kind: "knowledge_base", claims: 2 },
    { url: "https://www.practo.com/apollo-hospitals/reviews", fetched_at: daysAgo(0), source_kind: "web_mentions", claims: 3 },
    { url: "https://www.youtube.com/@ApolloHospitalsIndia", fetched_at: daysAgo(0), source_kind: "youtube_channel", claims: 1 },
  ],
};
