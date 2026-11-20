import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.evaluation.backtest.replay import engine_for, frozen_view
from app.evaluation.cli import EvaluationRuntime, command_registry
from app.evaluation.freeze.bundle import BundleStore
from app.evaluation.freeze.canary import run_canary
from app.evaluation.freeze.job import FreezeJob, FreezeRefused
from app.evaluation.freeze.purge import purge_expired_bundles
from app.evaluation.freeze.store import FreezeRepository
from app.models.tenancy import Client


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def create(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    container = runtime.container
    clients = [UUID(c) for c in args.client]
    if args.all:
        async with container.session_factory() as session:
            rows = await session.execute(
                select(Client.id).where(Client.archived_at.is_(None)).order_by(Client.id)
            )
            clients += list(rows.scalars())
    if not clients:
        print("name at least one --client or pass --all")
        return 2
    job = FreezeJob(container)
    as_of = _aware(args.as_of) if args.as_of else None
    failures = 0
    for client_id in clients:
        try:
            manifest = await job.run(client_id, as_of)
        except FreezeRefused as error:
            print(f"{client_id}\trefused\t{error}")
            failures += 1
            continue
        print(
            f"{client_id}\t{manifest.freeze_id}\t{manifest.as_of.isoformat()}\t{manifest.pit_level}\t"
            f"videos={manifest.record_counts.get('videos', 0)}\texpires={manifest.expires_at.isoformat()}"
        )
    return 1 if failures else 0


async def listed(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    async with runtime.container.session_factory() as session:
        rows = await FreezeRepository(session).listed(UUID(args.client) if args.client else None)
    for row in rows:
        print(f"{row.freeze_id}\t{row.client_id}\t{row.as_of.isoformat()}\t{row.pit_level}\t{row.status}")
    return 0


async def purge(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    container = runtime.container
    store = BundleStore.for_settings(container.settings)
    print(json.dumps(await purge_expired_bundles(container.session_factory, store, container.clock.now())))
    return 0


async def canary(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    settings = runtime.settings
    bundle = BundleStore.for_settings(settings).read(UUID(args.freeze_id), datetime.now(UTC))
    cfg = settings.evaluation.backtest
    engine = engine_for(settings)
    result = await asyncio.to_thread(
        run_canary,
        bundle.inputs,
        bundle.manifest.as_of,
        lambda inputs: frozen_view(
            engine, inputs, bundle, cfg.window_days, cfg.outcome_age_days
        ).fingerprint(),
        settings.evaluation.seed,
    )
    print(json.dumps({"passed": result.passed, "planted_rows": result.planted_rows}))
    return 0 if result.passed else 1


@command_registry.register("freeze")
def register(groups: Any) -> None:
    parser = groups.add_parser("freeze", help="point-in-time freezes of the inputs a backtest replays")
    commands = parser.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create", help="freeze one or more clients now (or at --as-of)")
    create_parser.add_argument("--client", action="append", default=[], help="client uuid; repeatable")
    create_parser.add_argument("--all", action="store_true", help="every non-archived client")
    create_parser.add_argument("--as-of", help="past instant; the freeze is then marked approximate")
    create_parser.set_defaults(handler=create)
    list_parser = commands.add_parser("list", help="list freezes")
    list_parser.add_argument("--client")
    list_parser.set_defaults(handler=listed)
    purge_parser = commands.add_parser("purge", help="delete bundles past their 30-day expiry")
    purge_parser.set_defaults(handler=purge)
    canary_parser = commands.add_parser("canary", help="run the leakage canary on one bundle")
    canary_parser.add_argument("--freeze-id", required=True)
    canary_parser.set_defaults(handler=canary)
