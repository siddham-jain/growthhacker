from datetime import UTC, date, datetime, timedelta
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...

    def today(self, tz: ZoneInfo) -> date: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self, tz: ZoneInfo) -> date:
        return self.now().astimezone(tz).date()


class FrozenClock:
    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock needs an aware datetime")
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def today(self, tz: ZoneInfo) -> date:
        return self._at.astimezone(tz).date()

    def advance(self, delta: timedelta) -> None:
        self._at += delta

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock needs an aware datetime")
        self._at = at.astimezone(UTC)
