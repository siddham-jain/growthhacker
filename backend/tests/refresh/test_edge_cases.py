from datetime import timedelta

from app.domain.common import VideoFormat
from app.domain.refresh import InsertionKind, RefreshStatus
from app.domain.topics import TopicKind
from app.services.refresh.engine import UNSUPPORTED_LANGUAGE
from app.services.refresh.inputs import RefreshClientOptions, StoredRefresh
from app.services.refresh.links.status import LinkStatusClass
from app.services.refresh.policy.domain import HealthcarePolicy
from app.services.refresh.triggers.base import TriggerKind
from tests.refresh import scenarios
from tests.refresh.fixtures import (
    NICHE_CHANNELS,
    NOW,
    FakeProbe,
    engine,
    match,
    metrics,
    news_signal,
    reference,
    topic_id,
    topic_ref,
    video,
)

TOOLS = topic_ref("AI tools")


def with_videos(data, records, speed: float = 300.0, topic=TOOLS):
    return data.model_copy(
        update={
            "own_videos": data.own_videos + records,
            "own_metrics": data.own_metrics + [metrics(record, velocity=speed) for record in records],
            "topic_matches": data.topic_matches + [match(record.video_id, topic, 0.85) for record in records],
            "topic_refs": data.topic_refs + ([topic] if topic not in data.topic_refs else []),
        }
    )


def ops_for(output, vid, kind=None):
    refresh = next((item for item in output.refreshes if item.video_id == vid), None)
    if refresh is None:
        return []
    ids = {trigger.id for trigger in refresh.triggers if kind is None or trigger.kind == kind}
    return [op for op in refresh.operations if set(op.trigger_ids) & ids]


def news_ops(output, vid):
    return ops_for(output, vid, TriggerKind.news_entity)


async def test_stale_year_links_to_the_newer_video_and_flags_the_title() -> None:
    old = video(
        "staleyear01",
        title="Best AI tools in 2025",
        description="My favourite AI tools.",
        published_days_ago=300,
    )
    new = video(
        "newyear0001",
        title="Best AI tools for 2026",
        description="This year's AI tools.",
        published_days_ago=60,
    )
    data = with_videos(with_videos(scenarios.runable(), [old]), [new], speed=2000.0)
    pool = [
        reference(f"year{index:07d}", f"AI tools 2026 roundup {index}", NICHE_CHANNELS[index % 3]).model_copy(
            update={"matched_topic_ids": [TOOLS.id]}
        )
        for index in range(3)
    ]
    output = await engine().run(data.model_copy(update={"reference_pool": data.reference_pool + pool}))
    refresh = next(item for item in output.refreshes if item.video_id == old.video_id)
    assert any(advisory.kind == "title_stale_year" for advisory in refresh.advisories)
    texts = [op.text for op in ops_for(output, old.video_id, TriggerKind.stale_year)]
    assert texts == ["Looking for the latest? Watch our 2026 update: https://youtu.be/newyear0001"]


async def test_shorts_get_an_advisory_instead_of_a_booking_link() -> None:
    short = video(
        "shortvid001",
        title="AI tools in 30 seconds",
        description="Quick AI tools tip.",
        published_days_ago=100,
        duration_s=30,
        fmt=VideoFormat.short_form,
    )
    output = await engine().run(with_videos(scenarios.runable(), [short]))
    refresh = next(item for item in output.refreshes if item.video_id == short.video_id)
    assert not any(op.kind is InsertionKind.insert and "runable.com" in op.text for op in refresh.operations)
    assert any(advisory.kind == "shorts_links_not_clickable" for advisory in refresh.advisories)


async def test_competitor_retracted_and_disputed_news_is_never_inserted() -> None:
    base = scenarios.runable()
    signal = base.news_signals[0]
    for update in (
        {"is_competitor_entity": True},
        {"verification_status": "retracted"},
        {"verification_status": "disputed"},
    ):
        data = base.model_copy(update={"news_signals": [signal.model_copy(update=update)]})
        assert news_ops(await engine().run(data), scenarios.AI_VIDEO) == []


def test_healthcare_news_only_for_non_clinical_events_from_official_sources() -> None:
    policy = HealthcarePolicy()
    drug = news_signal("Semaglutide", topic_ref_ids=[], kind="drug")
    event = news_signal("World Diabetes Day 2026", topic_ref_ids=[], kind="other")
    assert not policy.allows_news(drug, frozenset())
    assert policy.allows_news(event, frozenset())
    unofficial = event.model_copy(
        update={"sources": [source.model_copy(update={"tier": None}) for source in event.sources]}
    )
    assert not policy.allows_news(unofficial, frozenset())


async def test_description_at_the_byte_limit_gets_an_advisory_only() -> None:
    base = scenarios.runable()
    padded = base.own_videos[0].model_copy(
        update={"description": base.own_videos[0].description + "\n" + "मधुमेह " * 242}
    )
    assert len(padded.description.encode()) >= 4900
    data = base.model_copy(update={"own_videos": [padded, *base.own_videos[1:]]})
    output = await engine().run(data)
    refresh = next(item for item in output.refreshes if item.video_id == scenarios.AI_VIDEO)
    assert refresh.operations == []
    assert any("5000-byte limit" in advisory.message for advisory in refresh.advisories)


async def test_non_english_descriptions_get_link_fixes_only() -> None:
    hindi = video(
        "hindivid001",
        title="AI tools से पैसे कैसे कमाएं",
        description="इस वीडियो में AI tools के बारे में बताया गया है।",
        published_days_ago=200,
        default_language="hi",
    )
    output = await engine().run(with_videos(scenarios.runable(), [hindi]))
    assert news_ops(output, hindi.video_id) == []
    assert UNSUPPORTED_LANGUAGE in output.messages
    cta = ops_for(output, hindi.video_id, TriggerKind.missing_cta)
    assert [op.text for op in cta] == ["https://runable.com/signup"]


async def test_translated_descriptions_trigger_a_localization_advisory() -> None:
    base = scenarios.runable()
    localized = base.own_videos[0].model_copy(
        update={
            "localizations": {
                "hi": {"title": "t", "description": "d"},
                "es": {"title": "t", "description": "d"},
            }
        }
    )
    output = await engine().run(base.model_copy(update={"own_videos": [localized, *base.own_videos[1:]]}))
    refresh = next(item for item in output.refreshes if item.video_id == scenarios.AI_VIDEO)
    advisory = next(item for item in refresh.advisories if item.kind == "localizations_not_updated")
    assert "2 languages" in advisory.message


async def test_one_keyword_reaches_at_most_five_videos_and_never_the_same_sentence_everywhere() -> None:
    records = [
        video(
            f"aimany{index:05d}",
            title=f"AI tools workflow part {index}",
            description=f"Part {index} of my AI tools workflow series.\n\nhttps://runable.com/signup",
            published_days_ago=100 + index,
        )
        for index in range(7)
    ]
    output = await engine().run(with_videos(scenarios.runable(), records))
    news_videos = [refresh.video_id for refresh in output.refreshes if news_ops(output, refresh.video_id)]
    assert len(news_videos) == 3
    assert any(message.startswith("8 videos match 'Claude Fable 5.2'") for message in output.messages)
    assert any("same sentence is already suggested" in item.reason for item in output.dropped)


async def test_dismissed_suggestions_are_snoozed(sample_refresh) -> None:
    dismissed = StoredRefresh(
        refresh=sample_refresh.model_copy(update={"status": RefreshStatus.dismissed}),
        updated_at=NOW - timedelta(days=3),
    )
    data = scenarios.runable().model_copy(update={"previous_refreshes": [dismissed]})
    output = await engine().run(data)
    assert news_ops(output, scenarios.AI_VIDEO) == []
    assert any(item.reason == "you dismissed this recently" for item in output.dropped)


async def test_empty_description_gets_a_link_and_a_prompt() -> None:
    empty = video("emptydesc01", title="AI tools for agencies", description="", published_days_ago=150)
    output = await engine().run(with_videos(scenarios.runable(), [empty]))
    refresh = next(item for item in output.refreshes if item.video_id == empty.video_id)
    assert any(op.text.endswith("https://runable.com/signup") for op in refresh.operations)
    assert any("no description" in advisory.message for advisory in refresh.advisories)


async def test_upcoming_streams_are_skipped() -> None:
    upcoming = video(
        "upcoming001",
        title="AI tools live Q&A",
        description="Join us live.",
        published_days_ago=1,
        live="upcoming",
        fmt=VideoFormat.upcoming,
        duration_s=0,
    )
    output = await engine().run(with_videos(scenarios.runable(), [upcoming]))
    assert upcoming.video_id not in {refresh.video_id for refresh in output.refreshes}
    gate = next(item for item in output.videos if item.video_id == upcoming.video_id)
    assert not gate.public


async def test_dead_links_become_advisories_and_utm_is_opt_in() -> None:
    base = scenarios.runable()
    dead = video(
        "deadlink001",
        title="AI tools I use",
        description="My AI tools list: https://tools.example/list",
        published_days_ago=150,
    )
    probe = FakeProbe({"tools.example/list": LinkStatusClass.dead})
    data = with_videos(base, [dead]).model_copy(update={"options": RefreshClientOptions(utm_tagging=True)})
    output = await engine(probe=probe).run(data)
    refresh = next(item for item in output.refreshes if item.video_id == dead.video_id)
    assert any("Dead link (404 response)" in advisory.message for advisory in refresh.advisories)
    cta = ops_for(output, dead.video_id, TriggerKind.missing_cta)
    assert cta and "utm_source=youtube" in cta[0].text and "utm_campaign=deadlink001" in cta[0].text


async def test_keyword_only_in_tags_is_noted() -> None:
    base = scenarios.runable()
    tagged = base.own_videos[0].model_copy(update={"tags": ["Claude Fable 5.2", "ai"]})
    output = await engine().run(base.model_copy(update={"own_videos": [tagged, *base.own_videos[1:]]}))
    refresh = next(item for item in output.refreshes if item.video_id == scenarios.AI_VIDEO)
    trigger = next(item for item in refresh.triggers if item.kind == TriggerKind.news_entity)
    assert any("already in your tags" in note for note in trigger.evidence.notes)
    assert refresh.suggested_tags == []


async def test_topic_ids_link_news_to_videos() -> None:
    base = scenarios.runable()
    unrelated = base.news_signals[0].model_copy(update={"topic_ref_ids": [topic_id("cooking")]})
    output = await engine().run(base.model_copy(update={"news_signals": [unrelated]}))
    assert news_ops(output, scenarios.AI_VIDEO) == []


async def test_channels_without_steady_views_get_no_suggestions() -> None:
    data = scenarios.runable()
    slow = [metrics(record, velocity=1.0) for record in data.own_videos]
    output = await engine().run(data.model_copy(update={"own_metrics": slow}))
    assert output.eligibility.decision == "none"
    assert output.refreshes == []
    assert output.headline.startswith("No refresh needed right now")


async def test_refresh_can_be_turned_off() -> None:
    data = scenarios.runable().model_copy(update={"options": RefreshClientOptions(mode="off")})
    output = await engine().run(data)
    assert output.refreshes == []
    assert output.headline == "Description refresh is turned off in Settings."


def with_refs(data, phrase: str, topic_ids=()):
    pool = [
        reference(f"ref{index:08d}", f"{phrase} explained {index}", NICHE_CHANNELS[index % 3]).model_copy(
            update={"matched_topic_ids": list(topic_ids)}
        )
        for index in range(3)
    ]
    return data.model_copy(update={"reference_pool": data.reference_pool + pool})


async def test_keyword_below_the_fold_gets_a_top_line() -> None:
    agents = topic_ref("AI agents")
    intro = (
        "Welcome back! Today is something many of you have asked about for months, so grab a coffee, "
        "get comfortable and stay to the very end."
    )
    late = video(
        "belowfold01",
        title="AI agents for small teams",
        description=f"{intro}\n\nHere is how small teams use AI agents every day.",
        published_days_ago=200,
    )
    data = with_refs(with_videos(scenarios.runable(), [late], topic=agents), "AI agents")
    output = await engine().run(data)
    ops = ops_for(output, late.video_id, TriggerKind.above_fold_keyword)
    assert [op.text for op in ops] == ["AI agents for small teams."]
    assert ops[0].position.value == "top_line"


async def test_buyer_question_gap_adds_the_phrase_buyers_use() -> None:
    question = topic_ref("how much does an AI agent cost", TopicKind.buyer_question)
    record = video(
        "buyergap001",
        title="What AI agents really cost",
        description="How much you will pay for AI agents, from free tiers to teams.",
        published_days_ago=200,
    )
    data = with_refs(
        with_videos(scenarios.runable(), [record], topic=question), question.label, [question.id]
    )
    output = await engine().run(data)
    ops = ops_for(output, record.video_id, TriggerKind.buyer_question_gap)
    assert [op.text for op in ops] == ["What AI agents really cost: how much does an AI agent cost."]
    assert ops[0].claim_level.value == "L2_coverage"


async def test_missing_disclaimer_reuses_the_channels_own_text() -> None:
    data = scenarios.apollo()
    videos = [
        item.model_copy(update={"description": item.description.split("\n\n")[0]})
        if item.video_id == scenarios.MALFORMED_VIDEO
        else item
        for item in data.own_videos
    ]
    output = await engine().run(data.model_copy(update={"own_videos": videos}))
    refresh = next(item for item in output.refreshes if item.video_id == scenarios.MALFORMED_VIDEO)
    advisory = next(item for item in refresh.advisories if item.kind == "manual_review")
    assert "does not replace medical advice" in advisory.message
