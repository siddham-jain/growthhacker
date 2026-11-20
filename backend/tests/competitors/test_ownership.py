from datetime import UTC, datetime

from app.core.config import CompetitorSettings
from app.domain.competitors import CandidateSource, GeoRef
from app.domain.youtube import OwnershipLabel
from app.ports.llm import TaskId
from app.services.competitors.model import CandidateEntity, ChannelLead, LinkEvidence
from app.services.competitors.verify.adjudicator import LlmOwnershipAdjudicator
from app.services.competitors.verify.grouping import MultiChannelGrouper
from app.services.competitors.verify.verifier import OwnershipVerifier
from tests.competitors.apollo import (
    APOLLO_COACHING,
    FORTIS,
    MANIPAL,
    MAX,
    MEDANTA,
    apollo_profile,
    apollo_youtube,
)
from tests.competitors.conftest import make_context
from tests.competitors.fakes import NOW, FakeLLM, channel, video

YOUTUBE = apollo_youtube()


def entity(name, domain=None, country="IN", kinds=("web_search",)):
    return CandidateEntity(
        key="e1",
        brand_name=name,
        normalized_name=name.casefold(),
        official_domain=domain,
        geography=[GeoRef(country_iso2=country)],
        sources=[CandidateSource(kind=kind, ref="r", retrieved_at=NOW) for kind in kinds],
    )


def lead(channel_id, *evidence, record=None, uploads=True, method="site_link", guessed=False):
    record = record or YOUTUBE.channels[channel_id]
    items = (
        [YOUTUBE.videos[video_id] for video_id in YOUTUBE.uploads.get(record.uploads_playlist_id, [])]
        if uploads
        else []
    )
    return ChannelLead(
        channel_id, method, evidence=list(evidence), guessed=guessed, record=record, uploads=items
    )


def verify(target, *leads):
    for item in leads:
        target.leads[item.channel_id] = item
    OwnershipVerifier(CompetitorSettings()).verify_entity(target)
    return target


def test_medanta_site_link_and_embeds_verify():
    target = verify(
        entity("Medanta", "medanta.org"),
        lead(
            MEDANTA,
            LinkEvidence(name="site_links_channel", detail="medanta.org links /user/MedantaTheMedicity"),
            LinkEvidence(name="embed_majority", detail="8 of 8 embeds"),
        ),
    )
    assert target.leads[MEDANTA].label is OwnershipLabel.verified
    assert target.leads[MEDANTA].verdict.method == "link"


def test_fortis_vanity_and_web_search_verify_through_description():
    target = verify(
        entity("Fortis Healthcare", "fortishealthcare.com"),
        lead(
            FORTIS, LinkEvidence(name="websearch_youtube_hit", detail="web #1", rank=1), method="for_username"
        ),
    )
    verdict = target.leads[FORTIS].verdict
    assert verdict.label is OwnershipLabel.verified
    assert {"channel_links_site", "uploads_link_site", "websearch_youtube_hit"} <= {
        s.name for s in verdict.signals
    }


def test_max_by_channel_id_verifies():
    target = verify(
        entity("Max Healthcare", "maxhealthcare.in"),
        lead(MAX, LinkEvidence(name="websearch_youtube_hit", detail="#1", rank=1)),
    )
    assert target.leads[MAX].label is OwnershipLabel.verified


def test_guessed_handle_without_links_is_never_verified():
    target = verify(
        entity("Manipal Hospitals", "manipalhospitals.com"), lead(MANIPAL, guessed=True, uploads=False)
    )
    assert target.leads[MANIPAL].label is OwnershipLabel.likely


def test_apollo247_coaching_channel_is_rejected():
    target = verify(
        entity("Apollo 24|7", "apollo247.com"), lead(APOLLO_COACHING, guessed=True, method="for_handle")
    )
    verdict = target.leads[APOLLO_COACHING].verdict
    assert verdict.label is OwnershipLabel.rejected
    assert verdict.score < 0.2


def test_fan_and_topic_channels_are_rejected():
    fan = channel(
        "UCfortisFanPage000000001",
        "Fortis Healthcare Fan Page",
        description="Unofficial fan channel",
        videos=3,
    )
    topic = channel("UCfortisTopicChannel0001", "Fortis Healthcare - Topic", description="")
    target = verify(
        entity("Fortis Healthcare", "fortishealthcare.com"),
        lead(fan.channel_id, record=fan, uploads=False),
        lead(topic.channel_id, record=topic, uploads=False),
    )
    assert target.leads[fan.channel_id].label is OwnershipLabel.rejected
    assert target.leads[topic.channel_id].label is OwnershipLabel.rejected


def test_foreign_country_and_late_duplicate_penalties():
    copy = channel(
        "UCfortisCopyChannel00001",
        "Fortis Healthcare",
        description="fortishealthcare.com",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        country="US",
    )
    target = verify(
        entity("Fortis Healthcare", "fortishealthcare.com"),
        lead(FORTIS, LinkEvidence(name="site_links_channel", detail="footer")),
        lead(copy.channel_id, record=copy, uploads=False),
    )
    signals = {s.name: s for s in target.leads[copy.channel_id].verdict.signals}
    assert "late_duplicate" in target.leads[copy.channel_id].flags
    assert signals["sanity_penalty"].weight < -0.3


def test_user_asserted_counts_but_is_not_link_evidence():
    record = channel("UCuserAddedChannel000001", "Some Clinic")
    target = verify(
        entity("Some Clinic", kinds=("user",)),
        lead(
            record.channel_id,
            LinkEvidence(name="user_asserted", detail="you added"),
            record=record,
            uploads=False,
        ),
    )
    verdict = target.leads[record.channel_id].verdict
    assert verdict.method == "user" and verdict.label is not OwnershipLabel.verified


async def adjudicate(verdict_name, cited):
    record = channel("UCuncertainChannel000001", "Narayana Hospital Videos", description="hospital videos")
    target = entity("Narayana Health", "narayanahealth.org")
    item = ChannelLead(
        record.channel_id,
        "search_list",
        guessed=True,
        record=record,
        uploads=[video("aaaaaaaaaaa", record.channel_id, "Cardiology")],
    )
    target.leads[record.channel_id] = item
    settings = CompetitorSettings()
    OwnershipVerifier(settings).verify_entity(target)
    item.verdict = item.verdict.model_copy(update={"label": OwnershipLabel.uncertain, "score": 0.35})
    llm = FakeLLM(
        {
            TaskId.competitor_ownership_adjudication: lambda prompt: {
                "verdict": verdict_name,
                "reason": "r",
                "cited_signal_names": cited,
            }
        }
    )
    await LlmOwnershipAdjudicator(llm, settings).adjudicate(target, item, make_context())
    return item.verdict


async def test_adjudication_can_reach_likely_but_never_verified():
    verdict = await adjudicate("official", ["channel_title"])
    assert verdict.label is OwnershipLabel.likely and verdict.llm_reason == "r"
    assert (await adjudicate("fan", ["channel_title"])).label is OwnershipLabel.rejected


async def test_adjudication_citing_unknown_signals_is_discarded():
    assert (await adjudicate("official", ["invented_signal"])).label is OwnershipLabel.uncertain


def test_multi_channel_grouping_picks_main_and_marks_variants():
    regional = channel(
        "UCfortisBangaloreBranch1", "Fortis Bangalore", description="fortishealthcare.com", subscribers=5_000
    )
    person = channel("UCdrFortisSurgeonVideo01", "Dr. Ashok Rajgopal", description="fortishealthcare.com")
    target = entity("Fortis Healthcare", "fortishealthcare.com")
    for item in [
        lead(FORTIS),
        lead(regional.channel_id, record=regional),
        lead(person.channel_id, record=person),
    ]:
        item.verdict = (
            OwnershipVerifier(CompetitorSettings())
            .verdict(item, target, _ctx(target))
            .model_copy(update={"label": OwnershipLabel.likely})
        )
        target.leads[item.channel_id] = item
    primary = MultiChannelGrouper(apollo_profile()).group(
        target, {FORTIS: 10, regional.channel_id: 10, person.channel_id: 20}
    )
    assert primary.channel_id == FORTIS
    assert target.leads[regional.channel_id].variant == "regional"
    assert target.leads[person.channel_id].variant == "person"


def _ctx(target):
    from app.services.competitors.verify.signals import SignalContext

    settings = CompetitorSettings()
    return SignalContext(settings.ownership, frozenset({"IN"}), settings.uploads_link_share)


def test_channel_description_corroborates_a_guessed_domain():
    target = entity("Manipal Hospitals", "manipalhospitals.com", kinds=("llm",))
    target.flags.add("domain_unverified")
    verify(target, lead(MANIPAL, guessed=True))
    assert "channel_links_site" in {signal.name for signal in target.leads[MANIPAL].verdict.signals}
    assert "domain_unverified" not in target.flags and "domain_corroborated_by_channel" in target.flags
