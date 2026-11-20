"""apollo-style universe and synthetic paraphrased video texts (no youtube api data is stored here)."""

from app.domain.profile import FunnelStage
from tests.topics.fixtures import APOLLO_CLIENT, keyword, offering, profile, question

OFFERINGS = [
    offering(
        "off_knee",
        "Knee Replacement",
        aliases=["knee replacement surgery", "total knee replacement"],
        definition="Surgery that replaces a damaged knee joint with an implant.",
        priority=1,
    ),
    offering(
        "off_bypass",
        "Cardiac Bypass Surgery",
        aliases=["coronary artery bypass grafting", "heart bypass surgery"],
        definition="Open-heart surgery that reroutes blood around blocked coronary arteries (CABG).",
    ),
    offering(
        "off_ivf",
        "IVF",
        aliases=["in vitro fertilisation", "test tube baby"],
        definition="Fertility treatment where eggs are fertilised outside the body.",
    ),
    offering(
        "off_stone", "Kidney Stone Treatment", definition="Removal or breaking of stones in the kidney."
    ),
]
KEYWORDS = [
    keyword("kw_heart_attack", "heart attack"),
    keyword("kw_robotic", "robotic knee surgery", offering_ids=["off_knee"]),
    keyword("kw_ivf_cost", "IVF cost", offering_ids=["off_ivf"]),
]
QUESTIONS = [
    question(
        APOLLO_CLIENT,
        "How much does knee replacement cost?",
        stage=FunnelStage.decision,
        offering_ids=["off_knee"],
    ),
    question(APOLLO_CLIENT, "What are the symptoms of a heart attack?", stage=FunnelStage.awareness),
    question(APOLLO_CLIENT, "Who is eligible for IVF?", offering_ids=["off_ivf"]),
    question(APOLLO_CLIENT, "How long is recovery after bypass surgery?", offering_ids=["off_bypass"]),
]
PROFILE = profile(
    APOLLO_CLIENT,
    name="Apollo Hospitals",
    offerings=OFFERINGS,
    keywords=KEYWORDS,
    brand_terms=["Apollo", "Apollo Hospitals"],
    cities=["Chennai", "Delhi", "Hyderabad"],
    domain_kind="healthcare",
    description="Apollo offers Total Knee Replacement (TKR) and Coronary Artery Bypass Grafting (CABG).",
)
PROPOSALS = {
    "Knee Replacement": [
        {"text": "घुटने का प्रत्यारोपण", "lang": "hi", "script": "Deva", "kind": "translation"},
        {"text": "घुटने का ऑपरेशन", "lang": "hi", "script": "Deva", "kind": "translation"},
        {"text": "ghutne ka operation", "lang": "hi-Latn", "script": "Latn", "kind": "transliteration"},
        {"text": "arthritis", "lang": "en", "script": "Latn", "kind": "synonym"},
    ],
    "Cardiac Bypass Surgery": [
        {"text": "बायपास सर्जरी", "lang": "hi", "script": "Deva", "kind": "transliteration"},
        {"text": "dil ka bypass", "lang": "hi-Latn", "script": "Latn", "kind": "spelling_variant"},
        {"text": "दिल का बायपास", "lang": "hi", "script": "Deva", "kind": "translation"},
    ],
    "IVF": [{"text": "आईवीएफ", "lang": "hi", "script": "Deva", "kind": "transliteration"}],
    "heart attack": [
        {"text": "दिल का दौरा", "lang": "hi", "script": "Deva", "kind": "translation"},
        {"text": "hriday aaghat", "lang": "hi-Latn", "script": "Latn", "kind": "transliteration"},
    ],
    "Kidney Stone Treatment": [
        {"text": "पथरी का इलाज", "lang": "hi", "script": "Deva", "kind": "translation"},
        {"text": "pathri ka ilaj", "lang": "hi-Latn", "script": "Latn", "kind": "transliteration"},
    ],
}

# (key, title, description, tags, expected accepted labels)
VIDEOS = [
    (
        "en_knee_cost",
        "Knee Replacement Cost in India | Dr Rao explains",
        "What does a total knee replacement cost and what is included in the package?",
        ["knee replacement", "orthopaedics"],
        {"Knee Replacement", "How much does knee replacement cost?"},
    ),
    (
        "hi_knee_cost",
        "घुटने का प्रत्यारोपण कितने का होता है? | Knee Replacement Cost in India",
        "",
        [],
        {"Knee Replacement", "How much does knee replacement cost?"},
    ),
    (
        "hinglish_knee",
        "Ghutne ka operation kab karwana chahiye? Doctor se jaaniye",
        "",
        [],
        {"Knee Replacement"},
    ),
    (
        "hinglish_knee_variant",
        "ghutney ka operation ke baad recovery",
        "",
        [],
        {"Knee Replacement"},
    ),
    (
        "en_bypass",
        "Life after cardiac bypass surgery: recovery timeline",
        "How long does recovery take after heart bypass surgery? Weeks 1 to 6 explained.",
        [],
        {"Cardiac Bypass Surgery", "How long is recovery after bypass surgery?"},
    ),
    (
        "hi_bypass",
        "दिल का बायपास कब जरूरी है",
        "",
        [],
        {"Cardiac Bypass Surgery"},
    ),
    (
        "en_ivf",
        "IVF success rates and who should consider it",
        "Our fertility expert answers who is eligible for IVF and what the age criteria are.",
        ["ivf", "fertility"],
        {"IVF", "Who is eligible for IVF?"},
    ),
    (
        "hi_heart_attack",
        "दिल का दौरा: लक्षण और बचाव",
        "",
        [],
        {"heart attack", "What are the symptoms of a heart attack?"},
    ),
    (
        "hinglish_heart_attack",
        "Heart attack ke lakshan kya hai?",
        "",
        [],
        {"heart attack", "What are the symptoms of a heart attack?"},
    ),
    (
        "promo_event",
        "Apollo celebrates 1,000 robotic knee surgeries",
        "Our team marks a milestone.",
        [],
        {"robotic knee surgery"},
    ),
    (
        "negative_moon",
        "Apollo 11 moon landing documentary",
        "The story of the 1969 mission to the moon.",
        ["space", "nasa"],
        set(),
    ),
    (
        "boilerplate_only",
        "Doctors Day celebration at our hospital",
        "Centres of Excellence: Knee Replacement, Cardiac Bypass Surgery, IVF, Kidney Stone Treatment\nBook an appointment: 1860-500-1066",
        [],
        set(),
    ),
    (
        "stone_hinglish",
        "Pathri ka ilaj bina operation ke",
        "",
        [],
        {"Kidney Stone Treatment"},
    ),
    (
        "tags_only",
        "Morning walk tips",
        "",
        ["ivf", "heart bypass surgery"],
        set(),
    ),
    (
        "clickbait",
        "Is this the end? 😱",
        "",
        [],
        set(),
    ),
]

ADJUDICATION_RULES = {
    "Knee Replacement": ["knee replacement", "घुटने", "ghutne"],
    "Cardiac Bypass Surgery": ["bypass", "बायपास"],
    "IVF": ["IVF"],
    "heart attack": ["heart attack", "दिल का दौरा"],
    "Kidney Stone Treatment": ["pathri"],
}
