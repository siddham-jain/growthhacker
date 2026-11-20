from datetime import timedelta

import cv2
import numpy as np
import pytest

from app.domain.analytics import IntentClass
from app.domain.topics import TopicKind
from app.services.analytics.comments.lexicon import build_lexicon
from app.services.analytics.comments.signals import CommentSignalExtractor, PiiRedactor
from app.services.analytics.settings import AnalyticsEngineSettings, CommentSettings
from app.services.analytics.thumbnails.analyzer import ThumbnailTraitBuilder
from app.services.analytics.thumbnails.fetch import FetchedThumbnail, ThumbnailFetcher, YtimgImageSource
from app.services.analytics.thumbnails.pixels import colorfulness, crop_bars, detect_pillarbox, pixel_stats
from app.services.analytics.thumbnails.text import OcrPage, TextBox, recurring_text
from tests.analytics.factories import AS_OF, channel_id, make_comment, make_topic_ref, make_video

THUMBS = AnalyticsEngineSettings().thumbnails


def solid(height: int, width: int, rgb: tuple[int, int, int]) -> np.ndarray:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:] = rgb
    return image


def test_uniform_grey_has_no_contrast_or_colour():
    stats = pixel_stats(solid(360, 640, (128, 128, 128)), THUMBS)
    assert stats.rms_contrast == pytest.approx(0.0)
    assert stats.colorfulness == pytest.approx(0.0)
    assert stats.dominant_colour_count == 1
    assert stats.brightness_band == "mid"


def test_half_black_half_white_has_rms_contrast_one_half():
    image = solid(360, 640, (0, 0, 0))
    image[:, 320:] = 255
    stats = pixel_stats(image, THUMBS.model_copy(update={"letterbox_max_share": 0.0}))
    assert stats.rms_contrast == pytest.approx(0.5, abs=1e-3)
    assert stats.contrast_band == "high"


def test_pure_red_colourfulness_matches_the_formula():
    # rg = 255, yb = 127.5, no spread: m = 0.3 * sqrt(255^2 + 127.5^2)
    assert colorfulness(solid(10, 10, (255, 0, 0))) == pytest.approx(0.3 * np.hypot(255, 127.5))


def test_letterboxed_frame_is_cropped():
    image = solid(480, 640, (0, 0, 0))
    image[60:420] = (200, 30, 30)
    cropped, was_cropped = crop_bars(image, THUMBS, axis=0)
    assert was_cropped and cropped.shape[0] == 360


def test_pillarbox_bands_are_detected():
    image = solid(360, 640, (20, 20, 20))
    rng = np.random.default_rng(7)
    image[:, 220:420] = rng.integers(0, 255, size=(360, 200, 3), dtype=np.uint8)
    assert detect_pillarbox(image, THUMBS)
    assert not detect_pillarbox(rng.integers(0, 255, size=(360, 640, 3), dtype=np.uint8), THUMBS)


class FakeReader:
    engine = "fake"
    model = "fake-1"

    def __init__(self, latin: list[TextBox], devanagari: list[TextBox] | None = None) -> None:
        self.latin, self.devanagari, self.calls = latin, devanagari or [], []

    def read(self, image, script):
        self.calls.append(script)
        return self.latin if script == "latin" else self.devanagari


def box(text: str, conf: float, x: float, y: float, w: float = 300, h: float = 80) -> TextBox:
    return TextBox(points=((x, y), (x + w, y), (x + w, y + h), (x, y + h)), text=text, confidence=conf)


def encoded(image: np.ndarray) -> FetchedThumbnail:
    ok, data = cv2.imencode(".png", image[..., ::-1])
    assert ok
    return FetchedThumbnail(
        source_key="high", url="https://i.ytimg.com/vi/x/hqdefault.jpg", data=data.tobytes()
    )


def test_text_traits_filter_watermarks_and_brand_text():
    reader = FakeReader(
        [
            box("HEART ATTACK SIGNS", 0.95, 50, 100),
            box("Apollo", 0.9, 1000, 620),
            box("tiny", 0.99, 0, 0, h=10),
        ]
    )
    builder = ThumbnailTraitBuilder(reader, THUMBS)
    read = builder.read(encoded(solid(360, 640, (240, 240, 240))), devanagari_hint=False)
    traits = builder.traits("vid00000001", read, frozenset(), frozenset({"apollo", "hospitals"}), AS_OF)
    assert traits.text_present and traits.word_count == 3 and traits.word_count_band == "1-3"
    assert traits.text_box_count == 2 and not traits.brand_text_only
    assert traits.script_detected == "latin"
    watermark = frozenset(key for key in read.page.text_keys() if key[0] == "apollo")
    without_watermark = builder.traits("vid00000001", read, watermark, frozenset(), AS_OF)
    assert without_watermark.text_box_count == 1 and without_watermark.word_count == 3


def test_brand_only_and_unreadable_text():
    builder = ThumbnailTraitBuilder(FakeReader([box("Apollo Hospitals", 0.9, 50, 100)]), THUMBS)
    read = builder.read(encoded(solid(360, 640, (240, 240, 240))), devanagari_hint=False)
    assert builder.traits(
        "vid00000001", read, frozenset(), frozenset({"apollo", "hospitals"}), AS_OF
    ).brand_text_only

    blurry = FakeReader([box("~~~", 0.2, 50, 100)], devanagari=[box("~~", 0.3, 50, 100)])
    builder = ThumbnailTraitBuilder(blurry, THUMBS)
    read = builder.read(encoded(solid(360, 640, (240, 240, 240))), devanagari_hint=False)
    traits = builder.traits("vid00000001", read, frozenset(), frozenset(), AS_OF)
    assert blurry.calls == ["latin", "devanagari"]
    assert traits.text_present and traits.word_count is None and traits.script_detected == "unknown"


def test_devanagari_second_pass_keeps_the_better_reading():
    reader = FakeReader([box("Hdl", 0.55, 50, 100)], devanagari=[box("दिल की सेहत", 0.92, 52, 102)])
    builder = ThumbnailTraitBuilder(reader, THUMBS)
    read = builder.read(encoded(solid(360, 640, (240, 240, 240))), devanagari_hint=True)
    traits = builder.traits("vid00000001", read, frozenset(), frozenset(), AS_OF)
    assert traits.script_detected == "devanagari" and traits.word_count == 3


def test_recurring_text_needs_enough_thumbnails():
    page = OcrPage(boxes=(box("apollo", 0.9, 1000, 620),), width=1280, height=720)
    assert recurring_text([page] * 2, 0.4, 5) == {}
    assert recurring_text([page] * 6, 0.4, 5) == {("apollo", 8): 1.0}


class FakeSource:
    def __init__(self, available: dict[str, bytes]) -> None:
        self.available = available

    async def fetch(self, url: str) -> bytes | None:
        return self.available.get(url)


async def test_fetcher_falls_back_to_smaller_sizes():
    from app.domain.youtube import ThumbnailRef

    video = make_video(
        "vid00000001",
        channel_id(1),
        AS_OF,
        thumbnails={
            "maxres": ThumbnailRef(url="https://i.ytimg.com/vi/a/maxresdefault.jpg"),
            "high": ThumbnailRef(url="https://i.ytimg.com/vi/a/hqdefault.jpg"),
        },
    )
    fetched = await ThumbnailFetcher(
        FakeSource({"https://i.ytimg.com/vi/a/hqdefault.jpg": b"img"}), THUMBS.source_order
    ).fetch_best(video)
    assert fetched.source_key == "high"
    assert await ThumbnailFetcher(FakeSource({}), THUMBS.source_order).fetch_best(video) is None


async def test_image_source_refuses_other_hosts():
    with pytest.raises(ValueError, match="not allowed"):
        await YtimgImageSource(http=None).fetch("https://example.com/x.jpg")


def extractor(excerpts: bool = False, settings: CommentSettings | None = None) -> CommentSignalExtractor:
    question = make_topic_ref(
        9, "knee replacement cost", TopicKind.buyer_question, ["knee replacement", "cost"]
    )
    return CommentSignalExtractor(
        settings or CommentSettings(), build_lexicon("healthcare"), [question], ["Chennai"], excerpts
    )


COMMENTS = [
    "What is the cost of knee replacement surgery?",
    "Knee replacement ka kharcha kitna hai?",
    "इलाज का खर्च कितना है?",
    "Is this available in Chennai?",
    "Which doctor did the surgery? Dr Mehta?",
    "How to book an appointment",
    "Very informative video thanks",
    "Does it work for old patients? any side effects",
    "angioplasty vs bypass which is better",
    "Sunday open?",
    "great 👍👍",
    "Check my channel https://spam.example",
    "Crypto giveaway, message me",
]


def comments_for(video, texts):
    return [make_comment(video.video_id, i, text) for i, text in enumerate(texts)]


def test_intent_classes_across_languages():
    lexicon = build_lexicon("healthcare")
    assert IntentClass.cost in lexicon.classes("Knee replacement ka kharcha kitna hai?")
    assert IntentClass.cost in lexicon.classes("इलाज का खर्च कितना है?")
    assert IntentClass.cost in lexicon.classes("₹5000 only?")
    assert IntentClass.cost not in lexicon.classes("my heart rate is high")
    assert IntentClass.location in lexicon.classes("any branch in Chennai", ["Chennai"])
    assert IntentClass.doctor in lexicon.classes("Is Dr Mehta available")
    assert IntentClass.doctor not in build_lexicon(None).classes("Is Dr Mehta available")
    assert IntentClass.comparison in lexicon.classes("angioplasty vs bypass")


def test_comment_signals_count_intent_and_filter_spam():
    video = make_video("vid00000001", channel_id(1), AS_OF - timedelta(days=5), comments=40)
    signals = extractor().extract(video, comments_for(video, COMMENTS), frozenset(), AS_OF)
    assert signals.sampled_n == len(COMMENTS)
    assert signals.eligible_n == len(COMMENTS) - 2
    assert signals.sufficient
    assert signals.intent_counts[IntentClass.cost] == 3
    assert signals.buyer_intent_n >= 8
    assert signals.question_n >= 6
    assert signals.matched_buyer_questions == [("tr_000000000009", 1)]
    assert signals.sample_coverage == pytest.approx(len(COMMENTS) / 40)
    assert signals.excerpts == []
    assert "kharcha" not in signals.model_dump_json(exclude={"excerpts"})


def test_owner_comments_and_copy_paste_spam_are_dropped():
    video = make_video("vid00000001", channel_id(1), AS_OF)
    items = [make_comment(video.video_id, 0, "Thanks for watching, book now", owner=True)]
    spam = extractor().spam
    repeated = spam.repeated_texts(
        {f"v{i}": [make_comment(video.video_id, i, "Nice video bro")] for i in range(3)}
    )
    assert repeated == frozenset({"nice video bro"})
    items.append(make_comment(video.video_id, 1, "Nice video bro"))
    signals = extractor().extract(video, items, repeated, AS_OF)
    assert signals.eligible_n == 0 and not signals.sufficient and signals.question_share is None


def test_few_comments_keep_counts_without_shares():
    video = make_video("vid00000001", channel_id(1), AS_OF)
    signals = extractor().extract(video, comments_for(video, COMMENTS[:2]), frozenset(), AS_OF)
    assert not signals.sufficient
    assert signals.buyer_intent_n == 2 and signals.buyer_intent_share is None


def test_unanswered_questions_use_visible_owner_replies():
    video = make_video("vid00000001", channel_id(1), AS_OF)
    items = [
        make_comment(video.video_id, 0, "How much does it cost?", replies=2, owner_replied=True),
        make_comment(video.video_id, 1, "Where is the branch?", replies=1, owner_replied=False),
        make_comment(video.video_id, 2, "What is the price?", replies=0),
    ]
    signals = extractor().extract(video, items, frozenset(), AS_OF)
    assert signals.unanswered_question_n == 2 and signals.owner_reply_visible_n == 1


def test_disabled_comments_are_never_sufficient():
    video = make_video("vid00000001", channel_id(1), AS_OF, comments=None)
    assert not extractor().extract(video, [], frozenset(), AS_OF).sufficient


def test_excerpts_are_redacted_and_capped():
    video = make_video("vid00000001", channel_id(1), AS_OF)
    text = "Price? call me on +91 98765 43210 or mail a.b@example.com @someone " + "x" * 200
    signals = extractor(excerpts=True).extract(video, comments_for(video, [text] * 5), frozenset(), AS_OF)
    assert len(signals.excerpts) == 3
    assert all(len(e) <= 140 and "98765" not in e and "example.com" not in e for e in signals.excerpts)
    assert PiiRedactor().redact("ping @handle") == "ping [redacted]"
