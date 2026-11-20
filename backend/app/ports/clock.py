from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    # the only source of time; backtests inject a frozen clock
    def now(self) -> datetime: ...

    def today(self, tz: ZoneInfo) -> date: ...
