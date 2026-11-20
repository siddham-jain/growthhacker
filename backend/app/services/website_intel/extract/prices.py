import bisect
import re
from collections.abc import Iterator, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.website_intel.models import PriceCandidate, PricePeriod, PriceQualifier

CONTEXT_CHARS = 120
MAX_PRICES = 100
PRICE_TYPES = frozenset({"Offer", "AggregateOffer", "PriceSpecification", "UnitPriceSpecification"})
CURRENCY_CODES = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "rupees": "INR",
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "aed": "AED",
    "sgd": "SGD",
}
PERIODS: dict[str, PricePeriod] = {
    "mo": "per_month",
    "month": "per_month",
    "monthly": "per_month",
    "yr": "per_year",
    "year": "per_year",
    "annum": "per_year",
    "annually": "per_year",
    "yearly": "per_year",
    "session": "per_session",
    "visit": "per_session",
    "consultation": "per_session",
    "unit": "per_unit",
    "user": "per_unit",
    "seat": "per_unit",
}
_CURRENCY = r"(?:₹|US\$|\$|€|£|Rs\.?|INR|USD|EUR|GBP|AED|SGD)"
# indian lakh grouping (1,00,000) is tried before western grouping
_NUMBER = r"\d{1,3}(?:,\d{2})+,\d{3}(?:\.\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
PERIOD_GROUPS = ("p1", "p2", "p3", "q1", "q2", "q3")


def _period(prefix: str) -> str:
    return (
        rf"(?:\s*/\s*(?P<{prefix}1>mo|month|yr|year|annum|session|visit|unit|user|seat)\b"
        rf"|\s+(?:per|a|each)\s+(?P<{prefix}2>month|year|annum|session|visit|consultation|unit|user|seat)\b"
        rf"|\s+(?P<{prefix}3>monthly|annually|yearly)\b)?"
    )


_PRICE = re.compile(
    rf"(?<![A-Za-z])(?P<cur>{_CURRENCY})\s?(?P<a>{_NUMBER})(?:\s?/-)?"
    rf"(?:\s*(?:–|—|-|to)\s*(?:{_CURRENCY})?\s?(?P<b>{_NUMBER})(?:\s?/-)?)?"
    rf"{_period('p')}"
    rf"|(?<![\d,.])(?P<a2>{_NUMBER})\s?(?P<cur2>INR|USD|EUR|GBP|AED|SGD|rupees)\b{_period('q')}",
    re.IGNORECASE,
)
_FREE = re.compile(
    r"(?<!toll[\s-])\b(?:free\s+(?:consultation|trial|plan|tier|delivery|shipping|check-?up|demo|forever|"
    r"quote|estimate|of\s+(?:cost|charge))|for\s+free|100%\s+free)\b",
    re.IGNORECASE,
)
_CONTACT = re.compile(
    r"\b(?:contact\s+(?:us\s+)?for\s+(?:pricing|price|prices|a\s+quote|details)|price\s+on\s+request|"
    r"request\s+(?:a\s+)?quote|call\s+for\s+(?:price|pricing))\b",
    re.IGNORECASE,
)
_FROM_BEFORE = re.compile(
    r"(?:starting\s+(?:at|from)|starts\s+(?:at|from)|from|upwards\s+of)\s*$", re.IGNORECASE
)
_DISCOUNT_BEFORE = re.compile(r"(?:save|discount\s+of|flat)\s*$", re.IGNORECASE)
_FROM_AFTER = re.compile(r"^\s*(?:onwards|\+|and\s+above|upwards)", re.IGNORECASE)
_DISCOUNT_AFTER = re.compile(r"^\s*(?:off|discount)\b", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def _amount(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw).replace(",", ""))
    except InvalidOperation:
        return None


class PriceSignalExtractor:
    """deterministic price signals; json-ld offers win over text matches (plan 03 §5.7.7)."""

    def extract(
        self, text: str, *, page_url: str, json_ld: Sequence[dict[str, Any]] = ()
    ) -> list[PriceCandidate]:
        structured = list(
            {
                (price.currency, price.amount_min, price.amount_max, price.heading): price
                for price in self._from_json_ld(json_ld, page_url)
            }.values()
        )
        seen = {(price.currency, price.amount_min) for price in structured}
        headings = [(match.start(), match.group(1).strip()) for match in _HEADING.finditer(text)]
        positions = [position for position, _ in headings]
        found: dict[tuple[str, str | None], PriceCandidate] = {}
        for candidate in self._from_text(text, page_url, headings, positions):
            if (candidate.currency, candidate.amount_min) in seen and candidate.qualifier not in {
                "free",
                "contact_for_price",
            }:
                continue
            found.setdefault((candidate.raw_text, candidate.heading), candidate)
        return [*structured, *found.values()][:MAX_PRICES]

    def _from_text(
        self, text: str, page_url: str, headings: list[tuple[int, str]], positions: list[int]
    ) -> Iterator[PriceCandidate]:
        def heading_at(index: int) -> str | None:
            slot = bisect.bisect_right(positions, index) - 1
            return headings[slot][1] if slot >= 0 else None

        def context(start: int, end: int) -> str:
            return " ".join(text[max(0, start - CONTEXT_CHARS) : end + CONTEXT_CHARS].split())

        for match in _PRICE.finditer(text):
            currency_token = (match.group("cur") or match.group("cur2") or "").lower()
            low = _amount(match.group("a") or match.group("a2"))
            high = _amount(match.group("b"))
            period_token = next((match.group(name) for name in PERIOD_GROUPS if match.group(name)), None)
            start, end = match.span()
            yield PriceCandidate(
                raw_text=match.group(0).strip(),
                amount_min=low,
                amount_max=high if high is not None else low,
                currency=CURRENCY_CODES.get(currency_token),
                period=PERIODS.get(period_token.lower(), "unknown") if period_token else "unknown",
                qualifier=self._qualifier(text, start, end, ranged=high is not None),
                context=context(start, end),
                heading=heading_at(start),
                page_url=page_url,
            )
        for pattern, qualifier in ((_FREE, "free"), (_CONTACT, "contact_for_price")):
            for match in pattern.finditer(text):
                start, end = match.span()
                yield PriceCandidate(
                    raw_text=match.group(0).strip(),
                    amount_min=Decimal(0) if qualifier == "free" else None,
                    amount_max=Decimal(0) if qualifier == "free" else None,
                    qualifier=qualifier,
                    context=context(start, end),
                    heading=heading_at(start),
                    page_url=page_url,
                )

    def _qualifier(self, text: str, start: int, end: int, *, ranged: bool) -> PriceQualifier:
        before = text[max(0, start - 30) : start]
        after = text[end : end + 20]
        if _DISCOUNT_BEFORE.search(before) or _DISCOUNT_AFTER.search(after):
            return "discounted"
        if ranged:
            return "range"
        if _FROM_BEFORE.search(before) or _FROM_AFTER.search(after):
            return "from"
        return "exact"

    def _from_json_ld(self, json_ld: Sequence[dict[str, Any]], page_url: str) -> Iterator[PriceCandidate]:
        for entity in json_ld:
            yield from self._walk(entity, page_url, parent_name=None)

    def _walk(self, value: Any, page_url: str, parent_name: str | None) -> Iterator[PriceCandidate]:
        if isinstance(value, list):
            for item in value:
                yield from self._walk(item, page_url, parent_name)
            return
        if not isinstance(value, dict):
            return
        name = value.get("name") if isinstance(value.get("name"), str) else parent_name
        types = value.get("@type")
        type_names = {types} if isinstance(types, str) else set(types) if isinstance(types, list) else set()
        if type_names & PRICE_TYPES:
            candidate = self._offer(value, page_url, name)
            if candidate is not None:
                yield candidate
        for key, child in value.items():
            if key != "@type" and isinstance(child, dict | list):
                yield from self._walk(child, page_url, name)

    def _offer(self, offer: dict[str, Any], page_url: str, name: str | None) -> PriceCandidate | None:
        low = _amount(offer.get("lowPrice") if offer.get("lowPrice") is not None else offer.get("price"))
        high = _amount(offer.get("highPrice")) or low
        if low is None:
            return None
        currency = offer.get("priceCurrency") if isinstance(offer.get("priceCurrency"), str) else None
        amount_text = f"{low}" if high in {None, low} else f"{low}-{high}"
        qualifier: PriceQualifier = "free" if low == 0 else "range" if high != low else "exact"
        return PriceCandidate(
            raw_text=f"{currency} {amount_text}".strip() if currency else amount_text,
            amount_min=low,
            amount_max=high,
            currency=currency.upper() if currency else None,
            period="unknown",
            qualifier=qualifier,
            heading=name,
            origin="json_ld",
            page_url=page_url,
        )
