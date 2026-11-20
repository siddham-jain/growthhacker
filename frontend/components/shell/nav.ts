import {
  RiFolder3Line,
  RiLightbulbFlashLine,
  RiRadarLine,
  RiSettings3Line,
  RiShieldCheckLine,
  RiSunLine,
  RiUserStarLine,
  RiYoutubeLine,
  type RemixiconComponentType,
} from "@remixicon/react";

export interface NavItem {
  segment: string;
  label: string;
  question: string;
  icon: RemixiconComponentType;
}

// fixed order; each section answers one of the brief's questions
export const NAV_ITEMS: NavItem[] = [
  { segment: "today", label: "Today", question: "What should you do today?", icon: RiSunLine },
  { segment: "ideas", label: "Next videos", question: "What should you make next, and why will it convert?", icon: RiLightbulbFlashLine },
  { segment: "competitors", label: "Competitors", question: "How are your competitors doing? Cadence, performance, what's working.", icon: RiFolder3Line },
  { segment: "channel", label: "Your channel", question: "How are your videos doing against your own normal?", icon: RiYoutubeLine },
  { segment: "radar", label: "Niche radar", question: "What's moving in your niche right now?", icon: RiRadarLine },
  { segment: "hygiene", label: "Channel hygiene", question: "Small description fixes that need no evidence beyond the rule.", icon: RiShieldCheckLine },
  { segment: "creators", label: "Creators", question: "Who else in your niche could you work with?", icon: RiUserStarLine },
  { segment: "settings", label: "Settings", question: "Your business profile, topics, competitors and connections.", icon: RiSettings3Line },
];
