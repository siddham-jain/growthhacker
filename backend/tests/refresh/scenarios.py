"""synthetic fixtures modelled on the plan's live samples; no real api data is committed (30-day rule)."""

from app.domain.profile import BusinessModel, FreshnessSensitivity
from app.domain.topics import TopicKind
from app.services.refresh.inputs import RefreshInputs
from tests.refresh.fixtures import (
    NICHE_CHANNELS,
    action,
    inputs,
    location,
    match,
    metrics,
    news_signal,
    profile,
    reference,
    topic_ref,
    video,
    video_id,
)

FABLE = "Claude Fable 5.2"
AI_VIDEO = "runable0001"
RUNABLE_FOOTER = (
    "___________\nFollow Runable on X: https://x.com/runable\nJoin our Discord: https://discord.gg/runable"
)


def runable(*, refs: int = 4, confirmed: bool = False, extra_signals: tuple = ()) -> RefreshInputs:
    money = topic_ref("make money with AI")
    tools = topic_ref("AI tools")
    agents = topic_ref("AI agents")
    ai_description = (
        "In this video I share my exact workflow: 7 ways to make money with AI tools in 2026.\n"
        "\n"
        "00:00 Intro\n"
        "01:10 Freelancing with AI tools\n"
        "04:30 Running an AI content agency\n"
        "08:00 Selling templates\n"
        "\n"
        "Start building with Runable: https://runable.com/signup\n"
        f"{RUNABLE_FOOTER}"
    )
    videos = [
        video(
            AI_VIDEO,
            title="How to make money using AI in 2026",
            description=ai_description,
            published_days_ago=250,
        ),
        video(
            "runable0002",
            title="Runable product tour",
            description=f"A quick tour of the Runable workspace.\n\nTry it: https://runable.com/signup\n{RUNABLE_FOOTER}",
            published_days_ago=120,
        ),
        video(
            "runable0003",
            title="AI agents explained for beginners",
            description=f"What AI agents are and how teams use them.\n\n{RUNABLE_FOOTER}",
            published_days_ago=60,
        ),
        video(
            "runable0004",
            title="Automate your inbox with Runable",
            description=f"Three inbox automations in ten minutes.\n\n{RUNABLE_FOOTER}",
            published_days_ago=30,
        ),
    ]
    velocities = {AI_VIDEO: 900.0, "runable0002": 60.0, "runable0003": 120.0, "runable0004": 40.0}
    pool = [
        reference(
            f"fable{index:06d}", f"{FABLE} first look #{index}", NICHE_CHANNELS[index % 3], days_ago=1.5
        )
        for index in range(refs)
    ]
    signal = news_signal(FABLE, topic_ref_ids=[tools.id, money.id], kind="model")
    return inputs(
        profile=profile(
            name="Runable",
            domain="runable.com",
            sensitivity=FreshnessSensitivity.high,
            actions=[
                action(
                    "signup", "https://runable.com/signup", kind="signup", label="Start building with Runable"
                )
            ],
        ),
        topic_refs=[money, tools, agents],
        own_videos=videos,
        own_metrics=[metrics(item, velocity=velocities[item.video_id]) for item in videos],
        topic_matches=[
            match(AI_VIDEO, money, 0.9),
            match(AI_VIDEO, tools, 0.8),
            match("runable0003", agents, 0.85),
        ],
        news_signals=[signal, *extra_signals],
        reference_pool=pool,
        confirmations=[{"video_id": AI_VIDEO, "keyword": FABLE}] if confirmed else [],
    )


APOLLO_FOOTER = (
    "____________________\n"
    "Apollo Hospitals is the leading multi-specialty healthcare provider in India.\n"
    "This video is for general awareness and does not replace medical advice. Please consult your doctor.\n"
    "{facebook}\n"
    "Instagram: https://www.instagram.com/apollohospitals/\n"
    "X: https://x.com/HospitalsApollo"
)
FACEBOOK = "Facebook: https://www.facebook.com/TheApolloHospitals/"
MALFORMED_FACEBOOK = "https://www.facebook.com/https://www.facebook.com/TheApolloHospitals/"
BOOKING = "https://www.apollohospitals.com/book-doctor-appointment/"
HYDERABAD_BOOKING = "https://www.apollohospitals.com/hyderabad/book-appointment/"
MALFORMED_VIDEO = video_id("apollo", 7)
CSR_VIDEO = video_id("apollo", 50)
TOPICS = ("diabetic foot care", "knee replacement", "heart health", "kidney stones", "thyroid care")


def apollo() -> RefreshInputs:
    refs = [topic_ref(label, offering_ids=[f"off_{index}"]) for index, label in enumerate(TOPICS)]
    videos, measures, matches = [], [], []
    for index in range(1, 51):
        vid = video_id("apollo", index)
        topic = TOPICS[index % len(TOPICS)]
        place = "Apollo Hospitals Jubilee Hills, Hyderabad" if index % 2 else "Apollo Hospitals Chennai"
        body = f"{topic.capitalize()}: what to know and when to see a specialist at {place}."
        if index in (3, 4):
            body += f"\nBook an appointment: {BOOKING}"
        facebook = f"Facebook: {MALFORMED_FACEBOOK}" if vid == MALFORMED_VIDEO else FACEBOOK
        title = f"{topic.title()} | {place}"
        if vid == CSR_VIDEO:
            title, body = (
                "Apollo Aarambh - Model Anganwadi Centre launch",
                "Our CSR programme opens a new centre.",
            )
        record = video(
            vid,
            title=title,
            description=f"{body}\n\n{APOLLO_FOOTER.format(facebook=facebook)}",
            published_days_ago=30 + index * 6,
            channel_title="Apollo Hospitals",
            duration_s=540,
            default_language="en-IN",
        )
        videos.append(record)
        speed = 900.0 if vid == MALFORMED_VIDEO else 40.0 + index * 9
        measures.append(metrics(record, velocity=speed))
        if vid != CSR_VIDEO:
            matches.append(match(vid, refs[index % len(TOPICS)], 0.82))
    hyderabad = location("loc_hyd", "Apollo Hospitals Jubilee Hills", "Hyderabad", aliases=["Jubilee Hills"])
    chennai = location("loc_che", "Apollo Hospitals Greams Road", "Chennai")
    return inputs(
        profile=profile(
            name="Apollo Hospitals",
            domain="apollohospitals.com",
            sensitivity=FreshnessSensitivity.medium,
            business_model=BusinessModel.multi_location_service,
            geo_scope="national",
            regulated=True,
            domain_kind="healthcare",
            country="IN",
            locations=[hyderabad, chennai],
            actions=[
                action("book", BOOKING, label="Book a consultation with India's best doctors", primary=True),
                action(
                    "book_hyd",
                    HYDERABAD_BOOKING,
                    label="Book a consultation at Apollo Hospitals Jubilee Hills",
                    location_ids=["loc_hyd"],
                ),
            ],
        ),
        topic_refs=refs,
        own_videos=videos,
        own_metrics=measures,
        topic_matches=matches,
    )


DENTAL_DESCRIPTION = (
    "{body}\n"
    "\n"
    "Book your visit: https://smiledental.example/book\n"
    "Call us: +91 98765 43210\n"
    "#DentalCare #SmileDental"
)


def dental() -> RefreshInputs:
    whitening = topic_ref("teeth whitening")
    implants = topic_ref("dental implants")
    braces = topic_ref("clear aligners", TopicKind.offering)
    bodies = [
        ("How teeth whitening works", "Teeth whitening explained step by step.", whitening),
        ("Dental implants: what to expect", "Dental implants from consultation to recovery.", implants),
        ("Clear aligners vs braces", "Clear aligners compared with braces.", braces),
        ("Teeth whitening aftercare", "Teeth whitening aftercare tips.", whitening),
        ("Dental implants cost factors", "Dental implants and what affects the price.", implants),
    ]
    videos, measures, matches = [], [], []
    for index, (title, body, ref) in enumerate(bodies, start=1):
        vid = video_id("smile", index)
        record = video(
            vid,
            title=title,
            description=DENTAL_DESCRIPTION.format(body=body),
            published_days_ago=150 + index * 60,
            duration_s=300,
            default_language="en",
        )
        videos.append(record)
        measures.append(metrics(record, velocity=12.0 + index))
        matches.append(match(vid, ref, 0.8))
    return inputs(
        profile=profile(
            name="Smile Dental Clinic",
            domain="smiledental.example",
            sensitivity=FreshnessSensitivity.low,
            business_model=BusinessModel.local_service,
            geo_scope="local",
            actions=[action("book", "https://smiledental.example/book", label="Book your visit")],
        ),
        topic_refs=[whitening, implants, braces],
        own_videos=videos,
        own_metrics=measures,
        topic_matches=matches,
    )
