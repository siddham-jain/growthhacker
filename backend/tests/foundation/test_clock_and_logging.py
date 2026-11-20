from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from app.core.clock import Clock, FrozenClock, SystemClock
from app.core.logging import redaction_processor


def test_frozen_clock_is_aware_and_movable() -> None:
    clock = FrozenClock(datetime(2026, 11, 20, 7, 59, tzinfo=UTC))
    assert isinstance(clock, Clock)
    assert clock.today(ZoneInfo("America/Los_Angeles")).isoformat() == "2026-11-19"
    clock.advance(timedelta(hours=1))
    assert clock.today(ZoneInfo("America/Los_Angeles")).isoformat() == "2026-11-20"
    with pytest.raises(ValueError):
        FrozenClock(datetime(2026, 1, 1))


def test_system_clock_is_utc() -> None:
    assert SystemClock().now().tzinfo is UTC


def test_redaction_processor_hides_secrets_and_pii() -> None:
    event = redaction_processor(
        None,
        "info",
        {
            "event": "calling api for owner@example.com",
            "api_key": "AIzaSyD-not-a-real-key-000000000000000",
            "headers": {"Authorization": "Bearer abc.def", "accept": "json"},
            "dsn": "postgresql://growth:hunter2@localhost/db",
            "secret": SecretStr("x"),
            "note": "call +91 98765 43210 now",
            "count": 7,
        },
    )
    assert event["event"] == "calling api for [email]"
    assert event["api_key"] == "[redacted]"
    assert event["headers"] == {"Authorization": "[redacted]", "accept": "json"}
    assert "hunter2" not in event["dsn"]
    assert event["secret"] == "[redacted]"
    assert event["note"] == "call [phone] now"
    assert event["count"] == 7
