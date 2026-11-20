"""recorded-shape fixtures for the apollo hospitals golden test (ids and handles from the 2026-11-20 live check)."""

import uuid

from app.domain.profile import (
    BusinessIdentity,
    BusinessModel,
    BusinessProfile,
    ClaimMeta,
    ClaimOrigin,
    ClaimStatus,
    FreshnessProfile,
    FreshnessSensitivity,
    LocationSummary,
    NicheKeyword,
    Offering,
    ProfileQuality,
    RefreshPolicy,
    RelatedBrand,
)
from app.domain.web import FetchOutcome, SiteRef
from app.domain.youtube import SearchHit
from app.ports.llm import PromptInput, TaskId
from app.services.competitors.identity.normalize import normalize_name
from tests.competitors.fakes import (
    NOW,
    FakeCrawler,
    FakeLLM,
    FakeWebSearch,
    FakeYouTube,
    channel,
    hit,
    page,
    untrusted_entities,
    video,
)

CLIENT_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
WORKSPACE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
FORTIS = "UCn0ShMNqXI3kQP-Eoaf7olg"
MAX = "UCBKpVvogr2lH2y5IDEJWuFw"
MEDANTA = "UCSxY3aIGBg47WJASNYH6mUA"
MANIPAL = "UCSljO84JCPeaefyHe-8voIg"
NARAYANA = "UCr5mB2bbUrjEKeWNSYqDpdw"
PRACTO = "UCGlsl2YPuCvklhbUWu1vuqQ"
MAYO = "UCxK4pzc3fWbYjVGjjJ6vQTw"
CLEVELAND = "UCo9yWxfxQ1h7RZbQhVnmDmw"
APOLLO_COACHING = "UCapolloCoachingClasses1"
AIIMS_PREP = "UCaiimsExamPrepChannel01"
DOCTOR = "UCdrRajeshKumarOrtho0001"
EXPLAINER = "UChealthExplainedIndia01"
EXPECTED_COMPETITORS = {FORTIS, MAX, MEDANTA, MANIPAL, NARAYANA}


def meta() -> ClaimMeta:
    return ClaimMeta(
        origin=ClaimOrigin.user,
        status=ClaimStatus.confirmed,
        confidence=1.0,
        first_seen_version=1,
        updated_at=NOW,
    )


def offering(offering_id: str, name: str, priority: int, aliases: list[str] | None = None) -> Offering:
    return Offering(
        id=offering_id, name=name, kind="specialty", priority=priority, aliases=aliases or [], meta=meta()
    )


def keyword(keyword_id: str, term: str) -> NicheKeyword:
    return NicheKeyword(
        id=keyword_id,
        term=term,
        language="en",
        kind="procedure",
        tier="seed",
        source="page_text",
        weight=0.9,
        meta=meta(),
    )


def apollo_profile() -> BusinessProfile:
    identity = BusinessIdentity(
        name="Apollo Hospitals",
        name_meta=meta(),
        one_liner="Multi-specialty hospital chain in India",
        one_liner_meta=meta(),
        industry="healthcare",
        industry_meta=meta(),
        niche="multi-specialty hospital",
        business_model=BusinessModel.multi_location_service,
        geo_scope="national",
        primary_language="en",
        languages=["en", "hi"],
        country="IN",
        website=SiteRef(
            input_url="https://www.apollohospitals.com",
            canonical_origin="https://www.apollohospitals.com",
            registrable_domain="apollohospitals.com",
            scope_hosts=["www.apollohospitals.com"],
        ),
        website_domains=["apollohospitals.com"],
        brand_aliases=["Apollo"],
        primary_markets=["IN"],
        related_brands=[
            RelatedBrand(name="Apollo 24|7", domain="apollo247.com", relation="subsidiary", meta=meta()),
            RelatedBrand(name="Apollo Pharmacy", domain="apollopharmacy.in", relation="sister", meta=meta()),
        ],
    )
    return BusinessProfile(
        profile_id=uuid.UUID("00000000-0000-4000-8000-0000000000c1"),
        client_id=CLIENT_ID,
        version=1,
        status="confirmed",
        generated_at=NOW,
        identity=identity,
        offerings=[
            offering("o1", "Cardiology", 1, ["heart surgery"]),
            offering("o2", "Orthopaedics", 2, ["knee replacement"]),
            offering("o3", "Oncology", 2, ["cancer treatment"]),
            offering("o4", "Organ transplant", 3),
        ],
        locations=LocationSummary(
            count=6,
            cities=["Delhi", "Chennai", "Hyderabad", "Bengaluru", "Kolkata", "Mumbai"],
            regions=["Delhi", "Tamil Nadu", "Telangana", "Karnataka"],
            countries=["IN"],
        ),
        niche_keywords=[
            keyword("k1", "knee replacement"),
            keyword("k2", "heart surgery"),
            keyword("k3", "cancer treatment"),
        ],
        freshness=FreshnessProfile(
            sensitivity=FreshnessSensitivity.medium,
            sensitivity_score=0.5,
            score_components={},
            rationale="healthcare",
            refresh_policy=RefreshPolicy(
                news_scan_interval_hours=24,
                refresh_check_interval_days=30,
                description_refresh_eligible=True,
                min_signal_strength=0.5,
            ),
            meta=meta(),
        ),
        quality=ProfileQuality(
            claims_total=40,
            claims_verified=38,
            claims_dropped_unverified=2,
            injection_flags=0,
            thin_site=False,
            label="rich",
            completeness=0.9,
        ),
    )


BUYER_QUESTIONS = ["knee replacement cost in delhi", "best hospital for heart surgery"]


def uploads(channel_id: str, prefix: str, domain: str | None, count: int = 20) -> list:
    topics = [
        "Knee replacement recovery",
        "Heart surgery explained",
        "Cancer treatment options",
        "Health tips",
    ]
    return [
        video(
            f"{prefix[:7]}{index:04d}",
            channel_id,
            f"{topics[index % len(topics)]} | {prefix}",
            f"Book at {domain}" if domain and index % 2 == 0 else "Subscribe for more",
            days_ago=3 + index * 4,
        )
        for index in range(count)
    ]


def apollo_youtube() -> FakeYouTube:
    youtube = FakeYouTube()
    youtube.add_channel(
        channel(
            FORTIS,
            "Fortis Healthcare",
            handle="@fortishealthcareofficial",
            subscribers=837_000,
            description="Official channel. www.fortishealthcare.com",
        ),
        uploads(FORTIS, "fortisvid", "fortishealthcare.com"),
    )
    youtube.usernames["fortishealthcare"] = FORTIS
    youtube.add_channel(
        channel(
            MAX,
            "Max Healthcare",
            handle="@maxhealthcarevideos",
            subscribers=127_000,
            description="Visit maxhealthcare.in",
        ),
        uploads(MAX, "maxhcvideo", "maxhealthcare.in"),
    )
    youtube.usernames["maxhcvideos"] = MAX
    youtube.add_channel(
        channel(
            MEDANTA,
            "Medanta",
            handle="@medantahealthcare",
            subscribers=374_000,
            description="Medanta - The Medicity",
        ),
        uploads(MEDANTA, "medantavid", "medanta.org"),
    )
    youtube.usernames["medantathemedicity"] = MEDANTA
    youtube.add_channel(
        channel(
            MANIPAL,
            "Manipal Hospitals",
            handle="@ManipalHospitals",
            subscribers=361_000,
            description="manipalhospitals.com",
        ),
        uploads(MANIPAL, "manipalvid", "manipalhospitals.com"),
    )
    youtube.add_channel(
        channel(
            NARAYANA,
            "Narayana Health",
            handle="@NarayanaHealth",
            subscribers=877_000,
            description="narayanahealth.org",
        ),
        uploads(NARAYANA, "narayanav", "narayanahealth.org"),
    )
    youtube.add_channel(
        channel(
            PRACTO, "Practo", handle="@Practo", subscribers=135_000, description="Book doctors on practo.com"
        ),
        uploads(PRACTO, "practovid", "practo.com"),
    )
    youtube.add_channel(
        channel(
            MAYO,
            "Mayo Clinic",
            handle="@mayoclinic",
            subscribers=2_000_000,
            country="US",
            description="mayoclinic.org",
        ),
        uploads(MAYO, "mayovideos", "mayoclinic.org"),
    )
    youtube.usernames["mayoclinic"] = MAYO
    youtube.add_channel(
        channel(
            CLEVELAND,
            "Cleveland Clinic",
            handle="@clevelandclinic",
            subscribers=1_500_000,
            country="US",
            description="clevelandclinic.org",
        ),
        uploads(CLEVELAND, "clevelandv", "clevelandclinic.org"),
    )
    youtube.usernames["clevelandclinic"] = CLEVELAND
    youtube.add_channel(
        channel(
            APOLLO_COACHING,
            "APOLLO COACHING CLASSES SAMBHAJINAGAR",
            handle="@Apollo247",
            subscribers=31,
            videos=12,
            description="Coaching classes",
        ),
        uploads(APOLLO_COACHING, "apollocoach", None, count=3),
    )
    youtube.add_channel(
        channel(
            AIIMS_PREP,
            "AIIMS Exam Prep",
            handle="@aiimsexamprep",
            subscribers=4_000,
            videos=80,
            description="NEET and AIIMS coaching",
        ),
        uploads(AIIMS_PREP, "aiimsprepvi", None, count=5),
    )
    youtube.add_channel(
        channel(
            DOCTOR,
            "Dr. Rajesh Kumar Ortho",
            handle="@drrajeshortho",
            subscribers=60_000,
            description="Orthopaedic surgeon",
        ),
        uploads(DOCTOR, "drrajeshvid", None),
    )
    youtube.add_channel(
        channel(
            EXPLAINER,
            "Health Explained",
            handle="@healthexplained",
            subscribers=90_000,
            description="Health explainers",
        ),
        uploads(EXPLAINER, "healthexplv", None),
    )
    embeds = [video(f"medembed{index:03d}", MEDANTA, f"Medanta story {index}") for index in range(8)]
    for item in embeds:
        youtube.videos[item.video_id] = item
    youtube.videos["Mcek5LoOMZA"] = video("Mcek5LoOMZA", FORTIS, "Fortis patient story")

    def hits(*channel_ids: str) -> list[SearchHit]:
        return [
            SearchHit(kind="video", id=f"v{index:010d}", channel_id=cid, rank=index)
            for index, cid in enumerate(channel_ids)
        ]

    youtube.searches["knee replacement cost in delhi"] = hits(DOCTOR, PRACTO, EXPLAINER, DOCTOR)
    youtube.searches["best hospital for heart surgery"] = hits(PRACTO, DOCTOR, EXPLAINER)
    youtube.searches["knee replacement|heart surgery"] = hits(DOCTOR, MAYO)
    youtube.searches["cancer treatment"] = hits(MAYO)
    youtube.searches["AIIMS"] = [SearchHit(kind="channel", id=AIIMS_PREP, channel_id=AIIMS_PREP, rank=0)]
    return youtube


TRACXN = "https://tracxn.com/d/companies/apollo-hospitals/competitors"
GROWW = "https://groww.in/stocks/apollo-hospitals-peers"
TOP10 = "https://example-news.in/top-10-hospitals-india"


def apollo_web_search() -> FakeWebSearch:
    return FakeWebSearch(
        {
            "top competitors of Apollo Hospitals": [
                hit(
                    TRACXN,
                    "Apollo Hospitals competitors",
                    "Top competitors of Apollo Hospitals include Fortis Healthcare, Manipal Hospitals and Max Healthcare.",
                ),
                hit(
                    GROWW,
                    "Apollo Hospitals peers",
                    "Peers: Max Healthcare, Narayana Hrudayalaya, Dr. Reddy's Laboratories.",
                ),
            ],
            "largest multi-specialty hospital chains": [
                hit(
                    TOP10,
                    "Top 10 hospitals in India",
                    "Medanta - The Medicity in Gurugram ranks among the top hospitals.",
                ),
            ],
            "Fortis Healthcare official YouTube channel": [
                hit("https://www.youtube.com/fortishealthcare", "Fortis Healthcare - YouTube"),
                hit("https://www.youtube.com/watch?v=Mcek5LoOMZA", "Fortis patient story"),
            ],
            "Max Healthcare official YouTube channel": [
                hit(f"https://www.youtube.com/channel/{MAX}", "Max Healthcare - YouTube"),
                hit("https://www.youtube.com/user/maxhcvideos", "maxhcvideos"),
            ],
            "Mayo Clinic official YouTube channel": [
                hit("https://www.youtube.com/user/mayoclinic", "Mayo Clinic")
            ],
            "Cleveland Clinic official YouTube channel": [
                hit("https://www.youtube.com/user/clevelandclinic", "Cleveland Clinic")
            ],
            '"AIIMS"': [
                hit(
                    "https://www.aiims.edu/",
                    "AIIMS New Delhi",
                    "All India Institute of Medical Sciences, New Delhi",
                )
            ],
            '"Mayo Clinic"': [hit("https://www.mayoclinic.org/", "Mayo Clinic", "Mayo Clinic hospital")],
            '"Cleveland Clinic"': [
                hit("https://my.clevelandclinic.org/", "Cleveland Clinic", "Cleveland Clinic hospital")
            ],
            '"Zenith Superspeciality Hospitals"': [
                hit("https://example.com/zenith-bikes", "Zenith bikes", "Zenith bicycles for sale")
            ],
        }
    )


def apollo_crawler() -> FakeCrawler:
    blocked = dict(outcome=FetchOutcome.blocked, status=403)
    embeds = "".join(
        f'<iframe src="https://www.youtube.com/embed/medembed{index:03d}"></iframe>' for index in range(8)
    )
    return FakeCrawler(
        {
            "https://medanta.org/": page(
                "https://medanta.org/",
                f'<a href="https://www.youtube.com/user/MedantaTheMedicity">YouTube</a>{embeds}',
            ),
            "https://fortishealthcare.com/": page("https://fortishealthcare.com/", **blocked),
            "https://maxhealthcare.in/": page("https://maxhealthcare.in/", **blocked),
            "https://manipalhospitals.com/": page("https://manipalhospitals.com/", **blocked),
            "https://narayanahealth.org/": page("https://narayanahealth.org/", **blocked),
            "https://aiims.edu/": page("https://aiims.edu/", **blocked),
            "https://practo.com/": page("https://practo.com/", **blocked),
            "https://mayoclinic.org/": page("https://mayoclinic.org/", **blocked),
            "https://clevelandclinic.org/": page("https://clevelandclinic.org/", **blocked),
        }
    )


LLM_CANDIDATES = [
    (
        "Fortis Healthcare",
        "fortishealthcare.com",
        "direct",
        "Delhi",
        "IN",
        "Fortis Healthcare runs hospitals in Delhi offering cardiology and orthopaedics.",
    ),
    (
        "Max Healthcare",
        "maxhealthcare.in",
        "direct",
        "Delhi",
        "IN",
        "Max Healthcare hospitals in Delhi offer cardiology, oncology and orthopaedics.",
    ),
    (
        "Medanta",
        "medanta.org",
        "direct",
        "Gurugram",
        "IN",
        "Medanta in Gurugram offers cardiology and organ transplant.",
    ),
    (
        "Manipal Hospitals",
        "manipalhospitals.com",
        "direct",
        "Bengaluru",
        "IN",
        "Manipal Hospitals in Bengaluru offer cardiology and oncology.",
    ),
    (
        "Narayana Health",
        "narayanahealth.org",
        "direct",
        "Bengaluru",
        "IN",
        "Narayana Health in Bengaluru is known for cardiology.",
    ),
    (
        "AIIMS",
        "aiims.edu",
        "direct_public",
        "Delhi",
        "IN",
        "AIIMS Delhi is a public hospital offering cardiology.",
    ),
    (
        "Practo",
        "practo.com",
        "aggregator",
        None,
        "IN",
        "Practo lists doctors for cardiology and orthopaedics across India.",
    ),
    ("Apollo 24|7", "apollo247.com", "sister", None, "IN", "Apollo 24|7 is Apollo's digital health app."),
    (
        "Mayo Clinic",
        "mayoclinic.org",
        "benchmark",
        None,
        "US",
        "Mayo Clinic is a leading hospital offering cardiology and oncology.",
    ),
    (
        "Cleveland Clinic",
        "clevelandclinic.org",
        "benchmark",
        None,
        "US",
        "Cleveland Clinic is known for cardiology.",
    ),
    (
        "Zenith Superspeciality Hospitals",
        "zenithhospitals.in",
        "direct",
        "Delhi",
        "IN",
        "Zenith offers cardiology in Delhi.",
    ),
]

EXTRACTED = [
    (0, "Fortis Healthcare", "fortishealthcare.com", "include Fortis Healthcare, Manipal Hospitals"),
    (0, "Manipal Hospitals", "manipalhospitals.com", "Manipal Hospitals and Max Healthcare"),
    (0, "Max Healthcare", "maxhealthcare.in", "and Max Healthcare."),
    (1, "Narayana Hrudayalaya", "narayanahealth.org", "Narayana Hrudayalaya"),
    (1, "Dr. Reddy's Laboratories", "drreddys.com", "Dr. Reddy's Laboratories"),
    (2, "Medanta - The Medicity", "medanta.org", "Medanta - The Medicity in Gurugram"),
    (2, "Invented Hospital", "invented.example", "this quote is not in the result"),
]

# relation, offerings with verbatim quotes from the candidate's own snippets
RELATIONS = {
    "fortis healthcare": ("direct", [("o1", "yes", "cardiology"), ("o2", "yes", "orthopaedics")]),
    "max healthcare": (
        "direct",
        [("o1", "yes", "cardiology"), ("o3", "yes", "oncology"), ("o2", "yes", "orthopaedics")],
    ),
    "medanta the medicity": ("direct", [("o1", "yes", "cardiology"), ("o4", "yes", "organ transplant")]),
    "manipal hospitals": ("direct", [("o1", "yes", "cardiology"), ("o3", "yes", "oncology")]),
    "narayana hrudayalaya": ("direct", [("o1", "yes", "cardiology"), ("o2", "yes", "made-up quote")]),
    "aiims": ("direct_public", [("o1", "yes", "cardiology")]),
    "practo": ("aggregator", [("o1", "partial", "cardiology")]),
    "mayo clinic": ("benchmark", [("o1", "yes", "cardiology"), ("o3", "yes", "oncology")]),
    "cleveland clinic": ("benchmark", [("o1", "yes", "cardiology")]),
    "dr reddy s laboratories": ("supplier", []),
    "health explained": ("content", []),
    "dr rajesh kumar ortho": ("content", []),
}


def _candidates(prompt: PromptInput) -> dict:
    return {
        "candidates": [
            {
                "name": name,
                "official_domain_guess": domain,
                "relation_guess": relation,
                "geography": [{"city": city, "country_iso2": country}],
                "why": why,
            }
            for name, domain, relation, city, country, why in LLM_CANDIDATES
        ]
    }


def _extract(prompt: PromptInput) -> dict:
    return {
        "entities": [
            {"result_id": rid, "name": name, "domain": domain, "quote": quote}
            for rid, name, domain, quote in EXTRACTED
        ]
    }


def _classify(prompt: PromptInput) -> dict:
    entities = []
    for key, name in untrusted_entities(prompt):
        relation, offerings = RELATIONS.get(normalize_name(name), ("content", []))
        entities.append(
            {
                "key": key,
                "relation": relation,
                "offerings": [
                    {"offering_id": oid, "verdict": verdict, "quote": quote}
                    for oid, verdict, quote in offerings
                ],
            }
        )
    return {"entities": entities}


def _summaries(prompt: PromptInput) -> dict:
    return {"summaries": []}


def apollo_llm() -> FakeLLM:
    return FakeLLM(
        {
            TaskId.competitor_candidate_generation: _candidates,
            TaskId.competitor_entity_extraction: _extract,
            TaskId.competitor_relation_classification: _classify,
            TaskId.competitor_evidence_summary: _summaries,
            TaskId.competitor_ownership_adjudication: lambda prompt: {
                "verdict": "unrelated",
                "reason": "coaching channel",
                "cited_signal_names": ["channel_title"],
            },
        }
    )
