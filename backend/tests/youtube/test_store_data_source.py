from app.services.youtube.store import channel_snapshot_row, channel_to_row
from tests.analytics.factories import channel_id, make_channel


def test_api_channel_row_writes_every_field() -> None:
    row = channel_to_row(make_channel(channel_id(1)))

    assert row["view_count"] == 1_000_000 and row["country"] == "IN" and row["data_source"] == "data_api"


def test_scraped_channel_keeps_fields_the_scraper_cannot_see() -> None:
    record = make_channel(
        channel_id(1), subscribers=None, data_source="yt_dlp", hidden_subscriber_count=False
    )
    row = channel_to_row(record)

    for name in ("view_count", "video_count", "published_at", "country", "default_language"):
        assert name not in row
    assert "subscriber_count" not in row and "hidden_subscriber_count" not in row
    assert row["title"] == "Channel" and row["data_source"] == "yt_dlp"


def test_scraped_subscriber_count_is_written_when_seen() -> None:
    row = channel_to_row(make_channel(channel_id(1), subscribers=5_000, data_source="yt_dlp"))

    assert row["subscriber_count"] == 5_000


def test_history_row_leaves_scraper_blind_totals_empty() -> None:
    scraped = channel_snapshot_row(make_channel(channel_id(1), subscribers=5_000, data_source="yt_dlp"))
    api = channel_snapshot_row(make_channel(channel_id(1)))

    assert (scraped["subscriber_count"], scraped["view_count"], scraped["video_count"]) == (5_000, None, None)
    assert (api["view_count"], api["video_count"]) == (1_000_000, 500)
