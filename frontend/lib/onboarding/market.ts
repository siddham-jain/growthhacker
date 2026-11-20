import type { ProfileFact } from "@/lib/api/types";

// iso 3166-1 alpha-2, for the market picker; names come from Intl so they follow the browser language
const REGION_CODES =
  "AD AE AF AG AI AL AM AO AR AS AT AU AW AZ BA BB BD BE BF BG BH BI BJ BM BN BO BR BS BT BW BY BZ CA CD CF CG CH CI CL CM CN CO CR CU CV CY CZ DE DJ DK DM DO DZ EC EE EG ER ES ET FI FJ FM FR GA GB GD GE GH GI GL GM GN GQ GR GT GU GW GY HK HN HR HT HU ID IE IL IN IQ IR IS IT JM JO JP KE KG KH KI KM KN KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MG MH MK ML MM MN MO MR MT MU MV MW MX MY MZ NA NE NG NI NL NO NP NR NZ OM PA PE PG PH PK PL PR PS PT PW PY QA RO RS RU RW SA SB SC SD SE SG SI SK SL SM SN SO SR SS ST SV SY SZ TD TG TH TJ TL TM TN TO TR TT TV TW TZ UA UG US UY UZ VA VC VE VN VU WS XK YE ZA ZM ZW".split(" ");

export function countryName(code: string): string {
  if (!code) return "";
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

export function countryOptions(current: string): { code: string; name: string }[] {
  const codes = current && !REGION_CODES.includes(current) ? [current, ...REGION_CODES] : REGION_CODES;
  return codes.map((code) => ({ code, name: countryName(code) })).sort((a, b) => a.name.localeCompare(b.name));
}

const HOME_KINDS = new Set(["office", "hospital", "clinic", "store"]);

// "India · Hoshiarpur, Punjab": the country, then the main office, else the first physical place listed
export function marketLine(country: string, locations: ProfileFact[]): string {
  const physical = locations.filter((location) => location.detail && location.kind !== "service_area" && location.kind !== "virtual");
  const place = (physical.find((location) => HOME_KINDS.has(location.kind ?? "")) ?? physical[0])?.detail;
  const name = countryName(country) || "Market not detected yet";
  return place ? `${name} · ${place}` : name;
}
