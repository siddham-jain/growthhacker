import re
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal
from urllib.parse import unquote

import phonenumbers

from app.domain.web import SiteRef
from app.services.website_intel.acquisition.coverage import SITE_SOURCES
from app.services.website_intel.extract.contact import whatsapp_number
from app.services.website_intel.extract.cta import is_whatsapp
from app.services.website_intel.identity.knowledge import KbEntity
from app.services.website_intel.models import ExtractedPage

MarketSource = Literal["user", "cctld", "phones", "addresses", "currency", "language", "knowledge_base"]

# country-code tlds sold as generic names say nothing about where the business is
VANITY_TLDS = frozenset(
    {
        "ai",
        "io",
        "co",
        "me",
        "tv",
        "ly",
        "gg",
        "fm",
        "am",
        "to",
        "cc",
        "ws",
        "so",
        "sh",
        "vc",
        "la",
        "nu",
        "is",
    }
)
TLD_COUNTRY_OVERRIDES = {"uk": "GB"}
COUNTRY_NAMES = {
    "india": "IN",
    "bharat": "IN",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "canada": "CA",
    "australia": "AU",
    "singapore": "SG",
    "united arab emirates": "AE",
    "uae": "AE",
    "germany": "DE",
    "france": "FR",
    "pakistan": "PK",
    "bangladesh": "BD",
    "sri lanka": "LK",
    "nepal": "NP",
    "nigeria": "NG",
    "kenya": "KE",
    "south africa": "ZA",
    "malaysia": "MY",
    "philippines": "PH",
    "indonesia": "ID",
    "japan": "JP",
    "brazil": "BR",
    "mexico": "MX",
    "new zealand": "NZ",
    "saudi arabia": "SA",
    "switzerland": "CH",
    "netherlands": "NL",
    "italy": "IT",
    "spain": "ES",
    "china": "CN",
    "thailand": "TH",
    "vietnam": "VN",
    "egypt": "EG",
    "qatar": "QA",
    "oman": "OM",
    "ireland": "IE",
}
IN_STATES = (
    "andhra pradesh|arunachal pradesh|assam|bihar|chhattisgarh|goa|gujarat|haryana|himachal pradesh|jharkhand|"
    "karnataka|kerala|madhya pradesh|maharashtra|manipur|meghalaya|mizoram|nagaland|odisha|rajasthan|sikkim|"
    "tamil nadu|telangana|tripura|uttar pradesh|uttarakhand|west bengal|jammu and kashmir|chandigarh|puducherry"
)
US_STATES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|hawaii|idaho|illinois|"
    "indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|"
    "montana|nebraska|nevada|new hampshire|new jersey|new mexico|north carolina|north dakota|ohio|oklahoma|oregon|"
    "pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|wisconsin|wyoming"
)
IN_CITIES = (
    "new delhi|delhi|mumbai|bengaluru|bangalore|chennai|hyderabad|kolkata|pune|gurugram|gurgaon|noida|ahmedabad|"
    "jaipur|lucknow|patna|ranchi|indore|bhopal|guwahati|gorakhpur|kochi|bhubaneswar|nagpur|mysuru|visakhapatnam"
)
US_CITIES = (
    "san francisco|los angeles|new york city|seattle|boston|chicago|austin|houston|san diego|san jose|palo alto|"
    "mountain view"
)
# states and large cities whose name points at one country; ambiguous names (georgia, punjab) are left out
REGIONS = {**dict.fromkeys(IN_STATES.split("|"), "IN"), **dict.fromkeys(US_STATES.split("|"), "US")}
CITIES = {**dict.fromkeys(IN_CITIES.split("|"), "IN"), **dict.fromkeys(US_CITIES.split("|"), "US")}
CURRENCY_COUNTRIES = {
    "INR": "IN",
    "USD": "US",
    "GBP": "GB",
    "AUD": "AU",
    "CAD": "CA",
    "SGD": "SG",
    "AED": "AE",
    "PKR": "PK",
    "BDT": "BD",
    "LKR": "LK",
    "NPR": "NP",
    "NGN": "NG",
    "KES": "KE",
    "ZAR": "ZA",
    "MYR": "MY",
    "PHP": "PH",
    "JPY": "JP",
    "BRL": "BR",
    "MXN": "MX",
    "NZD": "NZ",
    "SAR": "SA",
}
DOMINANT_SHARE = 0.6
MIN_PHONES = 2
MIN_PLACE_NAMES = 3
PLACE_SHARE = 0.8
MIN_PRICES = 2
MAX_PAGES = 60
_RUPEE = re.compile(r"₹")
_PLACE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, {**REGIONS, **CITIES}), key=len, reverse=True)) + r")\b"
)


def _zone_table() -> tuple[dict[str, str], dict[str, frozenset[str]]]:
    table = resources.files("tzdata").joinpath("zoneinfo/zone1970.tab").read_text(encoding="utf-8")
    first: dict[str, str] = {}
    countries: dict[str, frozenset[str]] = {}
    for line in table.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        codes, _, zone = line.split("\t")[:3]
        countries[zone] = frozenset(codes.split(","))
        for code in codes.split(","):
            first.setdefault(code, zone)
    return first, countries


COUNTRY_TIMEZONES, ZONE_COUNTRIES = _zone_table()


def market_timezone(country: str, current: str | None) -> str | None:
    """keeps the current zone when it lies in the country (los angeles stays for us), else the country's first zone."""
    if current and country in ZONE_COUNTRIES.get(current, frozenset()):
        return current
    return COUNTRY_TIMEZONES.get(country)


def valid_region(code: str | None) -> str | None:
    upper = (code or "").strip().upper()
    return upper if upper in phonenumbers.SUPPORTED_REGIONS else None


def country_from_name(value: str | None) -> str | None:
    text = " ".join((value or "").replace(".", "").split()).casefold()
    return COUNTRY_NAMES.get(text) or (valid_region(text) if len(text) == 2 else None)


def region_country(name: str | None) -> str | None:
    return REGIONS.get(" ".join((name or "").split()).casefold())


@dataclass(frozen=True)
class MarketInference:
    country: str | None
    source: MarketSource | None = None
    detail: str = ""


def _dominant(counts: Counter[str], *, minimum: int, share: float = DOMINANT_SHARE) -> str | None:
    total = sum(counts.values())
    if not total:
        return None
    country, count = counts.most_common(1)[0]
    return country if count >= minimum and count / total >= share else None


def _addresses(blocks: Iterable[Any]) -> Iterator[dict[str, Any]]:
    for block in blocks:
        if isinstance(block, list):
            yield from _addresses(block)
        elif isinstance(block, dict):
            if "addressCountry" in block or "addressRegion" in block:
                yield block
            for value in block.values():
                if isinstance(value, dict | list):
                    yield from _addresses([value])


class MarketInferrer:
    """the client's market from evidence, strongest first; unknown rather than a guess (plan 03 §5.1.10)."""

    def infer(
        self,
        *,
        user_country: str | None,
        site: SiteRef | None,
        pages: Sequence[ExtractedPage],
        knowledge: KbEntity | None = None,
    ) -> MarketInference:
        if country := valid_region(user_country):
            return MarketInference(country, "user")
        site_pages = [page for page in pages if page.page.source_kind in SITE_SOURCES][:MAX_PAGES]
        for source, step in (
            ("cctld", lambda: self._cctld(site)),
            ("phones", lambda: self._phones(site_pages)),
            ("addresses", lambda: self._addresses(site_pages)),
            ("currency", lambda: self._currency(site_pages)),
            ("language", lambda: self._language(site_pages)),
            ("knowledge_base", lambda: self._knowledge(knowledge)),
        ):
            found = step()
            if found is not None:
                return MarketInference(found[0], source, found[1])  # type: ignore[arg-type]
        return MarketInference(None)

    @staticmethod
    def _cctld(site: SiteRef | None) -> tuple[str, str] | None:
        if site is None:
            return None
        tld = site.registrable_domain.rsplit(".", 1)[-1].lower()
        if len(tld) != 2 or tld in VANITY_TLDS:
            return None
        country = valid_region(TLD_COUNTRY_OVERRIDES.get(tld, tld))
        return (country, f".{tld}") if country else None

    @staticmethod
    def _phones(pages: Sequence[ExtractedPage]) -> tuple[str, str] | None:
        numbers: set[str] = set()
        for page in pages:
            for link in page.details.links:
                raw = unquote(link.url.strip())
                # only international numbers count; a national number needs the region we are looking for
                if raw.lower().startswith("tel:") and raw[4:].strip().startswith("+"):
                    numbers.add(raw[4:].strip())
                elif is_whatsapp(raw) and (whatsapp := whatsapp_number(raw)) is not None:
                    numbers.add("+" + whatsapp.lstrip("+"))
            numbers.update(
                phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
                for match in phonenumbers.PhoneNumberMatcher(page.page.text, None)
            )
        regions: Counter[str] = Counter()
        for number in numbers:
            try:
                parsed = phonenumbers.parse(number, None)
            except phonenumbers.NumberParseException:
                continue
            region = (
                phonenumbers.region_code_for_number(parsed) if phonenumbers.is_valid_number(parsed) else None
            )
            if region:
                regions[region] += 1
        country = _dominant(regions, minimum=MIN_PHONES)
        return (country, f"{regions[country]} of {sum(regions.values())} phone numbers") if country else None

    @staticmethod
    def _addresses(pages: Sequence[ExtractedPage]) -> tuple[str, str] | None:
        stated: Counter[str] = Counter()
        for page in pages:
            for address in _addresses(page.page.json_ld):
                value = address.get("addressCountry")
                name = value.get("name") if isinstance(value, dict) else value
                country = country_from_name(name if isinstance(name, str) else None) or region_country(
                    address.get("addressRegion") if isinstance(address.get("addressRegion"), str) else None
                )
                if country:
                    stated[country] += 1
        if country := _dominant(stated, minimum=1):
            return country, "structured addresses"
        places: dict[str, set[str]] = {}
        for page in pages:
            for match in _PLACE.finditer(page.page.text.casefold()):
                name = match.group(1)
                places.setdefault(REGIONS.get(name) or CITIES[name], set()).add(name)
        named = Counter({country: len(names) for country, names in places.items()})
        country = _dominant(named, minimum=MIN_PLACE_NAMES, share=PLACE_SHARE)
        return (country, ", ".join(sorted(places[country])[:4])) if country else None

    @staticmethod
    def _currency(pages: Sequence[ExtractedPage]) -> tuple[str, str] | None:
        currencies: Counter[str] = Counter()
        for page in pages:
            for price in page.details.prices:
                if country := CURRENCY_COUNTRIES.get((price.currency or "").upper()):
                    currencies[country] += 1
            currencies["IN"] += len(_RUPEE.findall(page.page.text))
        currencies = +currencies
        country = _dominant(currencies, minimum=MIN_PRICES)
        return (country, f"{currencies[country]} prices") if country else None

    @staticmethod
    def _language(pages: Sequence[ExtractedPage]) -> tuple[str, str] | None:
        regions: Counter[str] = Counter()
        for page in pages:
            tags = [page.page.lang or ""]
            tags += [
                item.lang
                for item in page.details.hreflang
                if item.url.rstrip("/") == page.page.url.rstrip("/")
            ]
            for tag in tags:
                parts = tag.replace("_", "-").split("-")
                if len(parts) >= 2 and (region := valid_region(parts[1])):
                    regions[region] += 1
        country = _dominant(regions, minimum=1)
        return (country, "page language tags") if country else None

    @staticmethod
    def _knowledge(entity: KbEntity | None) -> tuple[str, str] | None:
        if entity is None:
            return None
        countries = {code for name in entity.countries if (code := country_from_name(name))}
        return (countries.pop(), f"knowledge base {entity.qid}") if len(countries) == 1 else None
