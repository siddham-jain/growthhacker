from app.domain.common import VideoFormat
from app.domain.refresh import InsertionKind
from app.services.refresh.links.status import LinkStatusClass
from app.services.refresh.links.urls import normalise_url
from app.services.refresh.ops.templates import phone_label
from app.services.refresh.triggers.base import TriggerKind
from app.services.refresh.triggers.broken_link import UNVERIFIED_REPAIR
from app.services.refresh.triggers.hygiene import chapter_scaffold
from tests.refresh import scenarios
from tests.refresh.fixtures import FakeProbe, action, engine, match, metrics, video

REPAIRED_FACEBOOK = "https://www.facebook.com/TheApolloHospitals/"


def refresh_for(output, vid):
    return next(item for item in output.refreshes if item.video_id == vid)


def ops_of(refresh, kind):
    ids = {trigger.id for trigger in refresh.triggers if trigger.kind == kind}
    return [op for op in refresh.operations if set(op.trigger_ids) & ids]


def test_a_number_used_as_its_own_label_is_shown_once() -> None:
    assert phone_label("+91 4043441066", "+914043441066") == ("", "+91 4043441066")
    assert phone_label("08069049755", "+918069049755") == ("", "08069049755")
    assert phone_label("1860-500-1066", "+918069991061") == ("", "+918069991061")
    assert phone_label("1066", "https://wa.me/+911066") == ("", "https://wa.me/+911066")
    assert phone_label("Health Check Helpline", "+9118005701033") == (
        "Health Check Helpline",
        "+9118005701033",
    )


async def test_contact_line_uses_a_neutral_label_for_a_bare_number() -> None:
    data = scenarios.apollo()
    phone = action("call", "tel:+914043441066", kind="call", label="+91 4043441066", medium="phone")
    profile = data.profile.model_copy(
        update={"conversion_actions": [*data.profile.conversion_actions, phone]}
    )
    output = await engine().run(data.model_copy(update={"profile": profile}))
    texts = {op.text for refresh in output.refreshes for op in ops_of(refresh, TriggerKind.missing_contact)}
    assert texts == {"Call us: +91 4043441066"}


async def test_missing_chapters_gives_a_marked_scaffold_not_invented_chapters() -> None:
    output = await engine().run(scenarios.apollo())
    for refresh in output.refreshes:
        advisory = next(item for item in refresh.advisories if item.kind == "chapters_needed")
        assert advisory.message.endswith("\n0:00 Introduction\n[m:ss] [topic]\n[m:ss] [topic]")
        assert "no later than 8:50" in advisory.message
        assert not ops_of(refresh, TriggerKind.missing_chapters)
    assert "no later than 1:01:55" in chapter_scaffold(3725)


async def test_malformed_link_is_fixed_even_when_the_repair_cannot_be_opened() -> None:
    probe = FakeProbe({normalise_url(REPAIRED_FACEBOOK): LinkStatusClass.unverifiable})
    refresh = refresh_for(await engine(probe=probe).run(scenarios.apollo()), scenarios.MALFORMED_VIDEO)
    fixes = [op for op in refresh.operations if op.kind is InsertionKind.link_fix]
    assert [(op.old_text, op.text) for op in fixes] == [(scenarios.MALFORMED_FACEBOOK, REPAIRED_FACEBOOK)]
    assert UNVERIFIED_REPAIR in refresh.advisories


async def test_malformed_link_whose_repair_is_dead_gets_an_instruction() -> None:
    probe = FakeProbe({normalise_url(REPAIRED_FACEBOOK): LinkStatusClass.dead})
    refresh = refresh_for(await engine(probe=probe).run(scenarios.apollo()), scenarios.MALFORMED_VIDEO)
    assert not any(op.kind is InsertionKind.link_fix for op in refresh.operations)
    assert any("Remove or replace it in Studio" in advisory.message for advisory in refresh.advisories)


async def test_pasteable_fixes_outrank_advice_only_notes_under_the_run_cap() -> None:
    data = scenarios.apollo()
    topic = data.topic_refs[0]
    shorts = [
        video(
            f"apolloshrt{index}",
            title=f"{topic.label} in 30 seconds",
            description=f"{topic.label.capitalize()} tip.",
            published_days_ago=60,
            duration_s=30,
            fmt=VideoFormat.short_form,
        )
        for index in range(10)
    ]
    data = data.model_copy(
        update={
            "own_videos": data.own_videos + shorts,
            "own_metrics": data.own_metrics + [metrics(item, velocity=5000.0) for item in shorts],
            "topic_matches": data.topic_matches + [match(item.video_id, topic, 0.85) for item in shorts],
        }
    )
    output = await engine().run(data)
    assert len(output.refreshes) == 10
    assert all(refresh.operations for refresh in output.refreshes)
    assert any(item.video_id.startswith("apolloshrt") for item in output.dropped)
