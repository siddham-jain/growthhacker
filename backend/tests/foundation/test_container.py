from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.clock import FrozenClock
from app.core.container import build_container
from app.core.registry import PortRegistry, UnknownRegistrationError
from app.core.security import SecretCipher
from tests.conftest import make_settings

NOW = datetime(2026, 11, 20, tzinfo=UTC)


class FakeGateway:
    def __init__(self, container) -> None:
        self.container = container
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.db
async def test_container_boots_infrastructure(migrated_database: str) -> None:
    container = build_container(make_settings(), FrozenClock(NOW))
    try:
        async with container.uow(read_only=True) as uow:
            assert (await uow.session.execute(text("SELECT 1"))).scalar_one() == 1
        assert await container.redis.ping()
        assert container.clock.now() == NOW
    finally:
        await container.aclose()


async def test_adapters_resolve_lazily_from_registry() -> None:
    adapters = PortRegistry()
    adapters.register("youtube", "youtube_data_api")(FakeGateway)
    container = build_container(make_settings(), FrozenClock(NOW), adapters=adapters)
    gateway = container.adapter("youtube")
    assert isinstance(gateway, FakeGateway) and gateway.container is container
    assert container.adapter("youtube") is gateway
    await container.aclose()
    assert gateway.closed


async def test_override_replaces_adapter_for_tests() -> None:
    container = build_container(make_settings(), FrozenClock(NOW), adapters=PortRegistry())
    fake = object()
    container.override("llm", fake)
    assert container.adapter("llm") is fake
    with pytest.raises(UnknownRegistrationError):
        container.adapter("youtube")
    with pytest.raises(LookupError, match="no provider configured"):
        container.adapter("not_a_port")
    await container.aclose()


async def test_extra_ports_are_configurable_without_code_changes() -> None:
    adapters = PortRegistry()
    adapters.register("retention_refresher", "noop")(lambda container: "noop-refresher")
    settings = make_settings(adapters={"retention_refresher": "noop"})
    container = build_container(settings, adapters=adapters)
    assert container.adapter("retention_refresher") == "noop-refresher"
    await container.aclose()


def test_cipher_uses_configured_keys() -> None:
    settings = make_settings(
        security={"token_encryption_keys": [{"kid": "k1", "key": SecretCipher.generate_key()}]}
    )
    container = build_container(settings)
    assert container.cipher.primary_kid == "k1"
