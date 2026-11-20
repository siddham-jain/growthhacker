from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.evaluation.freeze.bundle import BundleStore
from app.evaluation.freeze.store import FreezeRepository
from app.evaluation.ops.hooks import nightly_hook_registry

logger = get_logger(__name__)


async def purge_expired_bundles(session_factory: Any, store: BundleStore, now: datetime) -> dict[str, int]:
    """deletes bundles at expiry, prunes rows past their own 28 days from the rest, and removes orphan files;
    eval_freeze rows stay as auditable 'purged' stubs."""
    deleted = 0
    async with session_factory() as session, session.begin():
        repository = FreezeRepository(session)
        expired = await repository.expired(now)
        known = {row.freeze_id for row in await repository.listed()}
        for row in expired:
            deleted += store.delete(row.freeze_id)
            await repository.mark_purged(row.freeze_id, now)
    orphans = [freeze_id for freeze_id in store.stored_ids() if freeze_id not in known]
    for freeze_id in orphans:
        store.delete(freeze_id)
    pruned = sum(store.prune(freeze_id, now) for freeze_id in store.stored_ids())
    if deleted or orphans or pruned:
        logger.info("eval.freeze_purged", bundles=deleted, orphans=len(orphans), rows_pruned=pruned)
    return {
        "bundles_deleted": deleted,
        "rows_expired": len(expired),
        "orphans_deleted": len(orphans),
        "rows_pruned": pruned,
    }


@nightly_hook_registry.register("freeze_bundle_purge")
async def freeze_bundle_purge(container: Any) -> Mapping[str, Any]:
    store = BundleStore.for_settings(container.settings)
    return await purge_expired_bundles(container.session_factory, store, container.clock.now())
