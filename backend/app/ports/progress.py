from typing import Protocol

from app.domain.runs import EventPayload, RunNotice


class ProgressReporter(Protocol):
    # bound to one stage; every call becomes a persisted RunEvent
    async def step(self, step: str, message: str, payload: EventPayload | None = None) -> None: ...

    async def progress(self, done: int, total: int | None, unit: str) -> None: ...

    async def insight(self, message: str, payload: EventPayload) -> None: ...

    async def notice(self, notice: RunNotice) -> None: ...
