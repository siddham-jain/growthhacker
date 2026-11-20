"""saas / ai-tools universe (runable-style) with synthetic paraphrased video texts."""

from app.domain.profile import FunnelStage
from tests.topics.fixtures import RUNABLE_CLIENT, keyword, offering, profile, question

OFFERINGS = [
    offering(
        "off_slides",
        "AI presentation maker",
        aliases=["AI slides", "AI PPT maker"],
        definition="Creates slide decks from a prompt.",
        priority=1,
    ),
    offering(
        "off_sites",
        "AI website builder",
        aliases=["build a website with AI"],
        definition="Generates and hosts a website from a description.",
    ),
    offering(
        "off_video",
        "AI video generator",
        aliases=["text to video AI"],
        definition="Turns a script or prompt into a finished video.",
    ),
]
KEYWORDS = [
    keyword("kw_agent", "AI agent"),
    keyword("kw_money", "make money with AI"),
    keyword("kw_money_hi", "AI se paise kamaye", language="hi-Latn"),
    keyword("kw_automation", "workflow automation"),
]
QUESTIONS = [
    question(RUNABLE_CLIENT, "How to make a website with AI?", offering_ids=["off_sites"]),
    question(RUNABLE_CLIENT, "Which AI tool can make videos from text?", stage=FunnelStage.consideration),
    question(RUNABLE_CLIENT, "How much does an AI presentation maker cost?", stage=FunnelStage.decision),
]
PROFILE = profile(
    RUNABLE_CLIENT,
    name="Runable",
    offerings=OFFERINGS,
    keywords=KEYWORDS,
    brand_terms=["Runable"],
    languages=["en", "hi"],
)
PROPOSALS = {
    "AI presentation maker": [
        {"text": "AI slide generator", "lang": "en", "script": "Latn", "kind": "synonym"},
        {"text": "pizza recipe", "lang": "en", "script": "Latn", "kind": "synonym"},
    ],
    "AI video generator": [{"text": "AI video maker", "lang": "en", "script": "Latn", "kind": "synonym"}],
    "AI agent": [{"text": "एआई एजेंट", "lang": "hi", "script": "Deva", "kind": "transliteration"}],
}

VIDEOS = [
    (
        "en_site",
        "I built a full website in 5 minutes with an AI agent",
        "Step by step: how to make a website with AI, no code needed.",
        ["ai website builder", "no code"],
        {"AI website builder", "AI agent", "How to make a website with AI?"},
    ),
    (
        "hinglish_money",
        "AI se paise kaise kamaye 2026 | sabse aasan tarika",
        "",
        [],
        {"AI se paise kamaye"},
    ),
    (
        "en_money",
        "How I make money with AI tools as a student",
        "",
        [],
        {"make money with AI"},
    ),
    (
        "en_slides",
        "Best AI presentation maker? I tested 5 tools",
        "Pricing for each AI slides tool starts at $10/month.",
        [],
        {"AI presentation maker", "How much does an AI presentation maker cost?"},
    ),
    (
        "en_video",
        "Text to video AI tutorial: make videos from a script",
        "",
        [],
        {"AI video generator", "Which AI tool can make videos from text?"},
    ),
    (
        "brand_only",
        "Runable review: is it worth it?",
        "",
        [],
        set(),
    ),
    (
        "off_topic",
        "Moon landing hoax debunked",
        "",
        [],
        set(),
    ),
    (
        "negated",
        "You don't need an AI agent for this",
        "",
        [],
        {"AI agent"},
    ),
]

ADJUDICATION_RULES = {
    "AI agent": ["AI agent"],
    "AI video generator": ["video"],
    "AI website builder": ["website"],
    "Which AI tool can make videos from text?": ["make videos from"],
}
