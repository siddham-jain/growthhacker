from decimal import Decimal

from app.services.website_intel.extract.prices import PriceSignalExtractor

PAGE = "https://example.com/"


def _prices(text: str, json_ld=()):
    return PriceSignalExtractor().extract(text, page_url=PAGE, json_ld=json_ld)


def test_indian_lakh_grouping_and_rupee_forms():
    found = _prices("Package ₹1,00,000 or Rs. 8,200/- or INR 12,300 or 500 rupees")
    assert [(price.amount_min, price.currency) for price in found] == [
        (Decimal(100000), "INR"),
        (Decimal(8200), "INR"),
        (Decimal(12300), "INR"),
        (Decimal(500), "INR"),
    ]


def test_ranges_periods_and_qualifiers():
    found = {
        price.raw_text: price
        for price in _prices(
            "Plans from $19/mo. Teams $10 - $20 per user. Annual €99 annually. Save £30 today. ₹999 onwards."
        )
    }
    assert found["$19/mo"].qualifier == "from" and found["$19/mo"].period == "per_month"
    ranged = found["$10 - $20 per user"]
    assert (ranged.amount_min, ranged.amount_max, ranged.qualifier, ranged.period) == (
        Decimal(10),
        Decimal(20),
        "range",
        "per_unit",
    )
    assert found["€99 annually"].period == "per_year"
    assert found["£30"].qualifier == "discounted"
    assert found["₹999"].qualifier == "from"


def test_free_and_contact_for_price_but_not_toll_free():
    found = _prices("Free consultation today. Call our toll free line. Contact us for pricing.")
    assert [(price.qualifier, price.raw_text) for price in found] == [
        ("free", "Free consultation"),
        ("contact_for_price", "Contact us for pricing"),
    ]


def test_context_and_nearest_heading():
    text = "# Packages\n## Executive Health Check\nComprehensive screening for ₹8,200 only."
    price = _prices(text)[0]
    assert price.heading == "Executive Health Check"
    assert "Comprehensive screening" in price.context


def test_json_ld_offers_win_over_text_duplicates():
    json_ld = [
        {
            "@type": "Product",
            "name": "Whitening",
            "offers": {"@type": "Offer", "price": "299", "priceCurrency": "usd"},
        }
    ]
    found = _prices("Whitening costs $299 and implants $1,500.", json_ld)
    assert [(price.origin, price.amount_min) for price in found] == [
        ("json_ld", Decimal(299)),
        ("text", Decimal(1500)),
    ]
    assert found[0].heading == "Whitening" and found[0].currency == "USD"


def test_aggregate_offer_is_a_range():
    json_ld = [{"@type": "AggregateOffer", "lowPrice": 10, "highPrice": 30, "priceCurrency": "EUR"}]
    price = _prices("", json_ld)[0]
    assert (price.qualifier, price.amount_min, price.amount_max) == ("range", Decimal(10), Decimal(30))


def test_plain_numbers_and_years_are_ignored():
    assert _prices("Founded in 2004 with 71 hospitals and 10,000 beds.") == []
