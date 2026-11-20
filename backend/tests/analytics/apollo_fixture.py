"""an apollo-like channel at onboarding (t0): last 50 uploads, one snapshot each, shaped on the 2026-11-20 sample."""

from datetime import timedelta

from app.domain.common import ChannelRole, VideoFormat
from tests.analytics.factories import (
    AS_OF,
    channel_id,
    make_channel,
    make_snapshot,
    make_video,
    scoped,
    video_id,
)

APOLLO = channel_id(1)
UPCOMING = {3, 17}
LIVE = {10: 1_040, 30: 6_945}
DUPLICATES = (5, 6)
SPIKE_TITLES = [
    "Apollo ProHealth check-up film",
    "Cancer care ad spot",
    "Joint pain? We can help",
    "Apollo 24/7 app promo",
    "Heart health campaign spot",
    "Women's wellness film",
]
SHORT_TOPICS = [
    "kidney care",
    "eye care",
    "child vaccines",
    "bone health",
    "skin care",
    "lung health",
    "sleep apnea",
    "thyroid",
    "migraine",
    "back pain",
    "liver care",
    "dental care",
]
LONG_TOPICS = [
    "knee replacement",
    "angioplasty",
    "diabetes diet",
    "blood pressure",
    "cataract surgery",
    "hernia repair",
    "spine surgery",
    "asthma",
    "ivf journey",
    "kidney stones",
    "obesity surgery",
    "hip replacement",
    "stroke rehab",
    "breast cancer screening",
    "fatty liver",
    "thyroid surgery",
    "piles treatment",
    "gallbladder stones",
    "sinus care",
    "arthritis",
    "epilepsy",
    "parkinson's care",
    "colon cancer",
    "heart valve",
]
SPIKES = {
    8: (188_000, 0.06),
    14: (212_000, 0.08),
    22: (270_000, 0.12),
    27: (330_000, 0.30),
    35: (445_000, 0.60),
    41: (899_000, 0.89),
}
ORGANIC = 12
ORGANIC_VIEWS = 61_913
SHORT_NORMALS = {1, 4, 9, 15, 19, 24, 29, 33, 38, 43, 46, 49}

BELOW = [
    500,
    560,
    610,
    650,
    700,
    740,
    780,
    820,
    860,
    900,
    940,
    980,
    1_010,
    1_040,
    1_070,
    1_100,
    1_130,
    1_160,
    1_190,
    1_220,
    1_250,
    1_270,
    1_290,
]
MIDDLE = [1_300, 1_340]
ABOVE = [
    1_360,
    1_420,
    1_480,
    1_560,
    1_640,
    1_720,
    1_810,
    1_900,
    2_000,
    2_150,
    2_300,
    2_480,
    2_660,
    2_880,
    3_100,
    3_400,
]
NORMAL_VIEWS = BELOW + MIDDLE + ABOVE


def apollo_vid(index: int) -> str:
    return video_id("ap", index)


def _published(index: int):
    return AS_OF - timedelta(days=2 + index * 1.7)


def apollo_videos_and_snapshots():
    videos, snapshots = [], {}
    others = [i for i in range(50) if i not in UPCOMING and i not in SPIKES and i != ORGANIC]
    # a fixed stride permutation spreads small and large counts over ages and segments
    order = sorted(others, key=lambda i: (i * 17) % 41)
    views_for = dict(zip(order, NORMAL_VIEWS, strict=True))
    for index in range(50):
        vid = apollo_vid(index)
        published = _published(index)
        if index in UPCOMING:
            videos.append(
                make_video(
                    vid,
                    APOLLO,
                    published + timedelta(days=5),
                    title="Live: Ask the cardiologist | Apollo Hospitals",
                    views=0,
                    likes=0,
                    comments=0,
                    duration_s=0,
                    fmt=VideoFormat.upcoming,
                    live_broadcast_content="upcoming",
                )
            )
            continue
        if index in DUPLICATES:
            published = _published(DUPLICATES[0])
            title = "360° Stroke Care | Apollo Hospitals"
            fmt, duration, rate = VideoFormat.short_form, 31, 4.6
        elif index in SPIKES:
            title, fmt, duration = SPIKE_TITLES[sorted(SPIKES).index(index)], VideoFormat.short_form, 30
            rate = SPIKES[index][1]
        elif index == ORGANIC:
            title, fmt, duration, rate = (
                "What are the early signs of a heart attack? | Dr Rao | Apollo Hospitals",
                VideoFormat.long_form,
                540,
                57.0,
            )
        elif index in LIVE:
            title, fmt, duration, rate = (
                "Live Q&A on diabetes care | Apollo Hospitals",
                VideoFormat.live,
                LIVE[index],
                3.6,
            )
        elif index in SHORT_NORMALS:
            topic = SHORT_TOPICS[sorted(SHORT_NORMALS).index(index)]
            title, fmt, duration, rate = (
                f"{topic.title()} at Apollo",
                VideoFormat.short_form,
                10 + (index * 7) % 80,
                3.5 + (index % 5) * 0.5,
            )
        else:
            topic = LONG_TOPICS[index % len(LONG_TOPICS)]
            title, fmt, duration, rate = (
                f"Understanding {topic} | Apollo Hospitals",
                VideoFormat.long_form,
                240 + index * 20,
                2.6 + (index % 5) * 0.5,
            )
        views = (
            SPIKES[index][0] if index in SPIKES else ORGANIC_VIEWS if index == ORGANIC else views_for[index]
        )
        likes = round(views * rate / 1000)
        extra = {}
        if index in LIVE:
            extra = {
                "live_actual_start_at": published,
                "live_actual_end_at": published + timedelta(seconds=LIVE[index]),
            }
        description = "Book an appointment: https://www.apollohospitals.com/book\nCall 1066 for emergencies."
        videos.append(
            make_video(
                vid,
                APOLLO,
                published,
                title=title,
                views=views,
                likes=likes,
                comments=index % 3,
                duration_s=duration,
                fmt=fmt,
                description=description,
                channel_title="Apollo Hospitals",
                **extra,
            )
        )
        snapshots[vid] = [make_snapshot(vid, AS_OF - timedelta(hours=1), views, likes, index % 3, published)]
    return videos, snapshots


def apollo_channel(role: ChannelRole = ChannelRole.own):
    return scoped(make_channel(APOLLO, "Apollo Hospitals", 1_200_000), role, ["apollohospitals.com"])


COMPETITORS = (channel_id(2), channel_id(3), channel_id(4))
COMPETITOR_VIDEOS = 40


def competitor_vid(channel_index: int, n: int) -> str:
    return video_id(f"c{channel_index}", n)


def is_competitor_outlier(n: int) -> bool:
    return n % 9 == 4


def _curve(day7: int, age: float) -> int:
    if age <= 7:
        return round(day7 * (age / 7) ** 0.5)
    return round(day7 * (1 + 0.3 * min(1.0, (age - 7) / 23)))


def competitor_videos_and_snapshots():
    """three tracked competitors with true day-1/3/7/30 snapshots; how-to titles carry their outliers."""
    videos, snapshots = [], {}
    for channel_index, cid in enumerate(COMPETITORS, start=2):
        for n in range(COMPETITOR_VIDEOS):
            vid = competitor_vid(channel_index, n)
            published = AS_OF - timedelta(days=1 + n * 2 + channel_index * 0.1)
            age_now = (AS_OF - published).total_seconds() / 86_400
            outlier = is_competitor_outlier(n)
            day7 = 6_500 if outlier else 900 + (n * 37 + channel_index * 53) % 400
            topic = LONG_TOPICS[(n + channel_index) % len(LONG_TOPICS)]
            title = (
                f"How to recover after {topic} surgery"
                if outlier
                else f"{topic.title()} explained by our team, part {n}"
            )
            views_now = _curve(day7, age_now)
            videos.append(
                make_video(
                    vid,
                    cid,
                    published,
                    title=title,
                    views=views_now,
                    likes=round(views_now * 0.02),
                    comments=3,
                    duration_s=300 + n * 7,
                    channel_title=f"Competitor {channel_index}",
                    description="Book a consultation: https://competitor.example/book" if n % 2 else "",
                    has_paid_product_placement=n == 5,
                )
            )
            ages = [a for a in (1, 3, 7, 30) if a < age_now] + [age_now - 0.01]
            snapshots[vid] = [
                make_snapshot(
                    vid,
                    published + timedelta(days=a),
                    _curve(day7, a),
                    round(_curve(day7, a) * 0.02),
                    3,
                    published,
                )
                for a in ages
            ]
    return videos, snapshots


def competitor_channels():
    return [
        scoped(
            make_channel(cid, f"Competitor {i}", 50_000 * i),
            ChannelRole.competitor,
            [f"competitor{i}.example"],
        )
        for i, cid in enumerate(COMPETITORS, start=2)
    ]
