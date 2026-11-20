import pytest

from app.services.answer_citations.urls import answer_urls, cited_videos, video_id_of
from tests.answer_citations.fakes import result


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/@ApolloHospitalsIndia", None),
        ("https://www.youtube.com/channel/UC0InVdvqNyNzKBl1-TL348A", None),
        ("https://www.youtube.com/watch?v=short", None),
        ("https://notyoutube.com/watch?v=dQw4w9WgXcQ", None),
        ("https://www.apollohospitals.com/knee-replacement", None),
    ],
)
def test_video_id_of(url: str, expected: str | None) -> None:
    assert video_id_of(url) == expected


def test_answer_urls_strip_trailing_punctuation_and_brackets() -> None:
    text = (
        "See (https://youtu.be/dQw4w9WgXcQ). Also https://example.com/a, and [https://youtu.be/aaaaaaaaaaa]"
    )
    assert answer_urls(text) == [
        "https://youtu.be/dQw4w9WgXcQ",
        "https://example.com/a",
        "https://youtu.be/aaaaaaaaaaa",
    ]
    assert answer_urls(None) == []


def test_cited_videos_keep_rank_order_and_first_mention() -> None:
    found = cited_videos(
        result(
            "https://www.apollohospitals.com/knee",
            "https://www.youtube.com/watch?v=bbbbbbbbbbb",
            "https://youtu.be/bbbbbbbbbbb",
            "https://www.youtube.com/shorts/ccccccccccc",
            answer="Watch https://youtu.be/ddddddddddd and https://youtu.be/ccccccccccc.",
        )
    )
    assert [(video.video_id, video.position, video.cited_in) for video in found] == [
        ("bbbbbbbbbbb", 2, "result"),
        ("ccccccccccc", 4, "result"),
        ("ddddddddddd", None, "answer"),
    ]


def test_no_youtube_sources_means_no_citations() -> None:
    assert cited_videos(result("https://example.com/a", answer=None)) == []
