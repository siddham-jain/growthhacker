from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.core.registry import Registry

# nightly housekeeping owned by other evaluation modules (e.g. freeze-bundle purge); takes the container
NightlyHook = Callable[[Any], Awaitable[Mapping[str, Any]]]
nightly_hook_registry: Registry[NightlyHook] = Registry("nightly_hook_registry")


async def run_nightly_hooks(
    container: Any, registry: Registry[NightlyHook] = nightly_hook_registry
) -> dict[str, Any]:
    # failures propagate so the cron run is marked failed rather than silently skipped
    return {name: dict(await hook(container)) for name, hook in registry.all().items()}
