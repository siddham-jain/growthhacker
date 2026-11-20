from dataclasses import dataclass, field
from typing import Any

import anyio

from app.api.auth import Authenticator
from app.api.errors import NotImplementedYet
from app.api.pagination import Cursors
from app.api.ratelimit import ApiRateLimiter
from app.api.services import api_service_registry
from app.api.signing import Signer
from app.api.sse import PubSubHub
from app.core.container import Container
from app.core.registry import UnknownRegistrationError
from app.pipeline.wiring import PipelineServices


@dataclass
class ApiState:
    container: Container
    pipeline: PipelineServices
    authenticator: Authenticator
    signer: Signer
    cursors: Cursors
    hub: PubSubHub
    limiter: ApiRateLimiter
    job_queue: Any
    alembic_heads: frozenset[str]
    shutdown: anyio.Event = field(default_factory=anyio.Event)
    _services: dict[str, Any] = field(default_factory=dict)

    @property
    def settings(self):
        return self.container.settings

    def override_service(self, name: str, implementation: Any) -> None:
        self._services[name] = implementation

    def service(self, name: str) -> Any:
        if name not in self._services:
            try:
                factory = api_service_registry.get(name)
            except UnknownRegistrationError:
                raise NotImplementedYet(
                    f"the '{name}' service is not wired into this deployment yet", extra={"service": name}
                ) from None
            self._services[name] = factory(self.container)
        return self._services[name]
