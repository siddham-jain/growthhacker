from datetime import UTC, datetime, timedelta

import pytest

from app.domain.common import FormatSegment
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.traits.base import TraitContext
from app.services.analytics.traits.description import DescriptionTraitExtractor, parse_chapters
from app.services.analytics.traits.timing import TimingTraitExtractor, resolve_timezone
from app.services.analytics.traits.title import TitleTraitExtractor
from tests.analytics.factories import AS_OF, channel_id, client_context, make_video

CHANNEL = channel_id(3)
TITLE = TitleTraitExtractor()
DESCRIPTION = DescriptionTraitExtractor()
TIMING = TimingTraitExtractor()


def ctx(**overrides) -> TraitContext:
    values = {
        "as_of": AS_OF,
        "client": client_context(),
        "settings": AnalyticsEngineSettings(),
        "segment": FormatSegment.long_form,
        "length_band": "8-15m",
    }
    return TraitContext(**(values | overrides))


def title_traits(text: str, **context):
    return TITLE.extract(make_video("vid00000001", CHANNEL, AS_OF, title=text), ctx(**context))


# expected trait subsets for english, hinglish, devanagari and apollo-style titles
TITLE_GOLDEN = [
    (
        "What are the early signs of a heart attack? | Dr Rao | Apollo Hospitals",
        {"is_question": True, "mentions_doctor": True, "separator": "pipe", "script": "latin"},
    ),
    ("How to lower cholesterol naturally", {"is_how_to": True, "is_question": True, "has_number": False}),
    (
        "Top 10 foods for a healthy heart",
        {"has_listicle_number": True, "has_superlative": True, "has_number": True},
    ),
    ("5 signs of diabetes you should not ignore", {"has_listicle_number": True, "is_question": False}),
    ("Call 1066 for emergencies", {"has_year": False, "has_number": True}),
    (
        "Best cardiologist in Chennai 2026",
        {"has_year": True, "year_is_current_or_next": True, "has_client_location": True},
    ),
    (
        "Heart rate explained: what is normal?",
        {"has_price_cost": False, "separator": "colon", "is_question": True},
    ),
    ("Cost of knee replacement in India", {"has_price_cost": True, "has_buyer_question_phrasing": True}),
    ("₹15,000 package for full body check-up", {"has_price_cost": True}),
    ("Angioplasty vs bypass: which is better?", {"has_buyer_question_phrasing": True, "is_question": True}),
    ("Diabetes kya hai? Kaise control kare", {"is_question": True, "script": "latin"}),
    ("Sugar kaise kam kare - expert tips", {"is_question": True, "separator": "dash"}),
    ("मधुमेह क्या है", {"is_question": True, "script": "devanagari", "caps_ratio": None}),
    ("डायबिटीज़ कैसे करें कंट्रोल", {"is_how_to": True, "script": "devanagari"}),
    ("Diabetes क्या है | Apollo", {"script": "mixed", "is_question": True}),
    ("MY KNEE SURGERY STORY", {"is_first_person": True, "caps_band": ">0.6", "all_caps_word_count": 3}),
    ("MRI and CT scan explained", {"all_caps_word_count": 0}),
    ("Heart health ❤️ tips 💪", {"has_emoji": True, "emoji_count": 2}),
    ("❤️❤️❤️", {"caps_ratio": None, "has_emoji": True}),
    ("[LIVE] Ask the doctor (Hindi)", {"has_brackets": True, "has_parentheses": True}),
    ("Physiotherapy near me | Apollo", {"has_niche_location": True, "has_client_location": False}),
    ("Stroke care in Hyderabad: step-by-step guide", {"is_how_to": True, "has_client_location": True}),
    ("Model X200 review", {"has_year": False, "has_number": True}),
    ("Cataract surgery side effects", {"has_buyer_question_phrasing": True}),
    ("Women's health: myths and facts", {"has_buyer_question_phrasing": True}),
    ("sabse accha hospital", {"has_superlative": True}),
    ("Doctor Mehta explains IVF", {"mentions_doctor": True}),
    ("डॉ. शर्मा से जानिए", {"mentions_doctor": True, "script": "devanagari"}),
]


@pytest.mark.parametrize(("title", "expected"), TITLE_GOLDEN)
def test_title_traits_golden(title, expected):
    traits = title_traits(title)
    assert {key: getattr(traits, key) for key in expected} == expected


def test_title_length_bands():
    assert title_traits("x" * 30).char_len_band == "0-30"
    assert title_traits("x" * 71).char_len_band == ">70"
    assert title_traits("  spaced   out  ").char_len == len("spaced out")


def description(text: str, **context):
    video = make_video("vid00000001", CHANNEL, AS_OF, description=text)
    return DESCRIPTION.extract(video, ctx(**context))


def test_chapters_follow_youtube_rules():
    valid = "0:00 Intro\n1:10 Symptoms\n4:30 Treatment\n12:00 Q&A"
    assert parse_chapters(valid).titles == ("Intro", "Symptoms", "Treatment", "Q&A")
    assert not parse_chapters("0:30 Intro\n1:10 Symptoms\n4:30 Treatment").valid
    assert not parse_chapters("0:00 Intro\n1:10 Symptoms").valid
    assert not parse_chapters("0:00 Intro\n0:05 Oops\n4:30 Treatment").valid
    assert parse_chapters("00:00 Start\n01:00:00 Hour\n01:05:00 After").valid


def test_description_conversion_hygiene():
    part = description(
        "Book an appointment today!\nhttps://www.apollohospitals.com/book\nhttps://bit.ly/abc\n"
        "WhatsApp: wa.me/919876543210\nCall 1800-500-1066 or 98765 43210\n"
        "ft. @drsharma #heart #health\nPaid partnership with HealthCo",
        is_client_video=True,
    )
    traits = part.traits
    assert traits.has_booking_cta and traits.has_whatsapp and traits.has_phone
    assert traits.has_shortener_link and traits.has_external_link
    assert traits.has_own_site_link and traits.has_client_site_link
    assert traits.link_domains == ["apollohospitals.com", "bit.ly"]
    assert traits.hashtag_count == 2 and not traits.hashtags_ignored
    assert traits.has_paid_disclosure_text
    assert traits.mentioned_handles == ["@drsharma"]
    assert part.malformed_links == 0
    assert "9876543210" not in traits.model_dump_json()


def test_phone_patterns_and_short_codes():
    assert description("Call +91 98765 43210").traits.has_phone
    assert description("Toll free 1800 123 4567").traits.has_phone
    assert description("Emergency? Dial 1066").traits.has_phone
    assert not description(
        "Emergency? Dial 1066", client=client_context(phone_short_codes=[])
    ).traits.has_phone
    assert not description("Order id 12345").traits.has_phone


def test_hashtag_overflow_and_malformed_links():
    tags = " ".join(f"#tag{i}" for i in range(61))
    assert description(tags).traits.hashtags_ignored
    broken = description("Visit http:/apollohospitals.com or https//apollo.com or www.apollo")
    assert broken.malformed_links >= 3
    assert DESCRIPTION.mined(broken)["description.has_malformed_link"] == frozenset({"true"})


def test_competitor_own_site_uses_its_domains():
    part = description(
        "https://www.maxhealthcare.in/doctors", channel_domains=frozenset({"maxhealthcare.in"})
    )
    assert part.traits.has_own_site_link and not part.traits.has_client_site_link


def test_timing_uses_client_timezone_with_fallbacks():
    video = make_video("vid00000001", CHANNEL, datetime(2026, 11, 20, 13, 0, tzinfo=UTC))
    traits = TIMING.extract(video, ctx())
    assert (traits.client_timezone, traits.publish_hour_local, traits.daypart) == (
        "Asia/Kolkata",
        18,
        "evening",
    )
    assert resolve_timezone(None, "IN") == "Asia/Kolkata"
    assert resolve_timezone(None, "US") == "UTC"
    assert resolve_timezone("Not/AZone", None) == "UTC"


def test_timing_across_a_dst_change():
    client = client_context(timezone="America/New_York", country="US")
    before = make_video("vid00000001", CHANNEL, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    after = make_video("vid00000002", CHANNEL, before.published_at + timedelta(hours=1))
    assert TIMING.extract(before, ctx(client=client)).publish_hour_local == 1
    assert TIMING.extract(after, ctx(client=client)).publish_hour_local == 1
