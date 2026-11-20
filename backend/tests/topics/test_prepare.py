from app.core.config import TopicEngineSettings
from app.services.topics.text.boilerplate import (
    BoilerplateProfile,
    BoilerplateStripper,
    ChannelBoilerplateProfiler,
    ChannelSample,
    line_key,
)
from tests.topics.fixtures import prepare, video_text

FOOTER = "Apollo Hospitals, Greams Road, Chennai\nCall 1860-500-1066 for appointments\nCentres of Excellence: Cardiology, Orthopaedics, Transplants"


def samples(count: int, footer: str = FOOTER) -> list[ChannelSample]:
    return [
        ChannelSample(
            description=f"Episode {index}: topic {index} explained\n{footer}", tags=["apollo", f"t{index}"]
        )
        for index in range(count)
    ]


def test_line_keys_mask_digits_and_urls() -> None:
    assert line_key("Episode 12 - https://apollo.com/x?1") == line_key("Episode 47 - http://bit.ly/abc")


def test_profiler_finds_identical_footer_and_channel_tags() -> None:
    profile = ChannelBoilerplateProfiler(TopicEngineSettings()).build("UC" + "a" * 22, samples(10))
    assert profile.profiled
    assert line_key("Call 1860-500-1066 for appointments") in profile.line_keys
    assert line_key("Episode 3: topic 3 explained") in profile.line_keys  # digits are masked, so it repeats
    assert "apollo" in profile.tags and "t1" not in profile.tags


def test_profiler_tolerates_varying_dates() -> None:
    varied = [
        ChannelSample(description=f"Health talk\nRecorded on {day} March 2026 at Apollo Chennai", tags=[])
        for day in range(1, 11)
    ]
    profile = ChannelBoilerplateProfiler(TopicEngineSettings()).build("UC" + "b" * 22, varied)
    assert line_key("Recorded on 9 March 2026 at Apollo Chennai") in profile.line_keys


def test_profiler_needs_five_videos() -> None:
    profile = ChannelBoilerplateProfiler(TopicEngineSettings()).build("UC" + "c" * 22, samples(4))
    assert not profile.profiled
    assert profile.sample_size == 4


def test_block_rule_drops_the_footer_tail() -> None:
    profile = BoilerplateProfile("x", "v1", line_keys=frozenset({"a", "b", "c"}))
    flags = BoilerplateStripper(TopicEngineSettings()).boilerplate_flags(
        ["topic", "a", "b", "variant", "c"], profile
    )
    assert flags == [False, True, True, True, True]


def test_boilerplate_is_stripped_and_recorded(settings) -> None:
    profile = ChannelBoilerplateProfiler(settings).build("UC" + "a" * 22, samples(10))
    video = video_text("Knee replacement recovery", f"Knee replacement recovery takes six weeks.\n{FOOTER}")
    prepared = prepare(video, settings, profile)
    assert "Cardiology" not in prepared.fields.description_head
    reasons = {span.reason for span in prepared.removed_spans}
    assert "boilerplate" in reasons and reasons <= {"boilerplate", "cta"}
    for span in prepared.removed_spans:
        assert video.description[span.start : span.end] in FOOTER


def test_link_lists_ctas_and_inline_junk_are_removed(settings) -> None:
    description = (
        "00:00 Intro\n01:30 Who needs a knee replacement\n"
        "Dr Rao explains knee replacement. Mail info@apollo.com or @ApolloHospitals\n"
        "Also watch: Knee pain causes\nhttps://youtu.be/abc123def45\nSubscribe for more videos\n"
        "Disclaimer: this video is for information only and is not medical advice."
    )
    prepared = prepare(video_text("Knee replacement #KneeReplacement #Ortho", description), settings)
    assert prepared.fields.chapters == ["Intro", "Who needs a knee replacement"]
    assert "Also watch" not in prepared.fields.description_head
    assert "info@apollo.com" not in prepared.fields.description_head
    assert "@ApolloHospitals" not in prepared.fields.description_head
    assert prepared.fields.title == "Knee replacement"
    assert prepared.fields.hashtags == ["Knee Replacement", "Ortho"]
    reasons = {span.reason for span in prepared.removed_spans}
    assert {"link_list", "cta", "email", "handle"} <= reasons
    for span in prepared.removed_spans:
        if span.field == "description":
            assert description[span.start : span.end].strip()


def test_channel_wide_hashtags_are_dropped(settings) -> None:
    profile = BoilerplateProfile("UC" + "a" * 22, "v1", hashtags=frozenset({"#apollohospitals"}))
    prepared = prepare(video_text("IVF explained #ApolloHospitals #IVF"), settings, profile)
    assert prepared.fields.hashtags == ["IVF"]
    assert any(span.reason == "channel_hashtag" for span in prepared.removed_spans)


def test_quality_flags(settings) -> None:
    assert "emoji_only_title" in prepare(video_text("😱😱😱"), settings).quality_flags
    assert "hashtag_only_title" in prepare(video_text("#knee #surgery"), settings).quality_flags
    flags = prepare(video_text("Is this the end?"), settings).quality_flags
    assert {"very_short", "no_topical_text", "boilerplate_unprofiled"} <= set(flags)
    stuffed = prepare(video_text("Knee pain", tags=[f"tag{index}" for index in range(45)]), settings)
    assert "tag_stuffed" in stuffed.quality_flags
    assert "long_description" in prepare(video_text("Knee", "knee " * 700), settings).quality_flags
    assert "unsupported_script" in prepare(video_text("முழங்கால் மாற்று அறுவை சிகிச்சை"), settings).quality_flags


def test_description_head_and_rest_respect_limits(settings) -> None:
    sentence = "Knee replacement recovery explained in detail. "
    prepared = prepare(video_text("Knee", sentence * 100), settings)
    assert len(prepared.fields.description_head) <= settings.head_chars
    assert (
        len(prepared.fields.description_head) + len(prepared.fields.description_rest)
        <= settings.desc_max_chars + 1
    )


def test_prepared_hash_changes_with_boilerplate_version(settings) -> None:
    video = video_text("Knee replacement")
    first = prepare(video, settings, BoilerplateProfile("c", "v1"))
    second = prepare(video, settings, BoilerplateProfile("c", "v2"))
    assert first.prepared_hash != second.prepared_hash
    assert prepare(video, settings, BoilerplateProfile("c", "v1")).prepared_hash == first.prepared_hash
