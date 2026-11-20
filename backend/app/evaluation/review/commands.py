import argparse
import asyncio
import uuid
from pathlib import Path
from typing import Any

from app.evaluation.cli import EvaluationRuntime, command_registry
from app.evaluation.labelling.commands import packs_dir
from app.evaluation.packs.ids import batch_uuid
from app.evaluation.reporting.markdown import write_report
from app.evaluation.review.gate_tuning import propose_gate_changes
from app.evaluation.review.importer import import_ratings
from app.evaluation.review.pack import render_review_pack
from app.evaluation.review.render import render_review_summary
from app.evaluation.review.rubric import rubric_markdown
from app.evaluation.review.sampler import ReviewSampleDesigner
from app.evaluation.review.scorer import summarise_review
from app.evaluation.review.source import PublishedCardSource
from app.evaluation.review.store import ReviewStore


async def export(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    settings = runtime.settings
    container = runtime.container
    now = container.clock.now()
    clients = await PublishedCardSource(container.session_factory, args.client).load()
    if not any(client.cards for client in clients):
        print("no published recommendation cards; run an analysis for the demo clients first")
        return 1
    seed = args.seed if args.seed is not None else settings.evaluation.seed
    design = ReviewSampleDesigner(settings.evaluation.review).design(
        clients, seed, settings.policy.retention_days, batch_uuid(now)
    )
    async with container.session_factory() as session, session.begin():
        await ReviewStore(session).save_design(design)
    out = Path(args.out) if args.out else packs_dir(runtime)
    out.mkdir(parents=True, exist_ok=True)
    for rater in args.rater:
        html = render_review_pack(
            design,
            participant=rater,
            seed=seed,
            batch_size=settings.evaluation.labelling.pack_batch_size,
            now=now,
        )
        path = out / f"review-pack-{str(design.batch_id)[:8]}-{rater}.html"
        path.write_text(html, encoding="utf-8")
        print(f"wrote {path}")
    decoys = sum(item.is_decoy for item in design.items)
    print(f"batch {design.batch_id}: {len(design.items) - decoys} cards + {decoys} decoys")
    for note in design.notes:
        print(f"note: {note}")
    return 0


async def import_file(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    container = runtime.container
    batch_id = uuid.UUID(args.batch)
    async with container.session_factory() as session, session.begin():
        store = ReviewStore(session)
        items = await store.items(batch_id)
        if not items:
            print(f"batch {batch_id} has no items")
            return 1
        result = import_ratings(
            await asyncio.to_thread(Path(args.file).read_text, encoding="utf-8"),
            rater=args.rater,
            known_item_ids={i.item_id for i in items},
            rated_at=container.clock.now(),
        )
        await store.save_ratings(result.ratings)
    print(f"imported {len(result.ratings)} ratings ({result.skipped} unanswered rows skipped)")
    return 0


async def score(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    settings = runtime.settings
    container = runtime.container
    async with container.session_factory() as session, session.begin():
        store = ReviewStore(session)
        batch_id = uuid.UUID(args.batch) if args.batch else await store.latest_batch_id()
        if batch_id is None:
            print("no review batch exists yet")
            return 1
        items = await store.items(batch_id)
        ratings = await store.ratings([i.item_id for i in items])
        summary = summarise_review(
            batch_id, items, ratings, settings.evaluation.review, settings.evaluation.seed
        )
        proposals = propose_gate_changes(items, ratings, settings.evaluation.review)
        await store.save_proposals(batch_id, proposals)
    out = Path(args.out) if args.out else settings.evaluation.docs_dir / "review-summary.md"
    payload = {
        "summary": summary.model_dump(mode="json"),
        "proposals": [p.model_dump(mode="json") for p in proposals],
    }
    for path in write_report(out, render_review_summary(summary, proposals), payload):
        print(f"wrote {path}")
    return 0


async def rubric(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    out = Path(args.out) if args.out else runtime.settings.evaluation.docs_dir / "review-rubric.md"
    for path in write_report(out, rubric_markdown()):
        print(f"wrote {path}")
    return 0


@command_registry.register("review")
def register(groups: Any) -> None:
    parser = groups.add_parser("review", help="human review packs of recommendation cards")
    commands = parser.add_subparsers(dest="command", required=True)
    exporter = commands.add_parser("export", help="sample 30 cards + 6 decoys and write one pack per rater")
    exporter.add_argument("--rater", action="append", required=True, help="repeat for each rater")
    exporter.add_argument("--client", action="append", type=uuid.UUID, help="limit to these clients")
    exporter.add_argument("--seed", type=int)
    exporter.add_argument("--out", help="directory for the html packs")
    exporter.set_defaults(handler=export)
    importer = commands.add_parser("import", help="import a review pack's exported csv")
    importer.add_argument("--batch", required=True)
    importer.add_argument("--rater", required=True)
    importer.add_argument("file")
    importer.set_defaults(handler=import_file)
    scorer = commands.add_parser("score", help="review summary, agreement and gate tuning proposals")
    scorer.add_argument("--batch")
    scorer.add_argument("--out")
    scorer.set_defaults(handler=score)
    writer = commands.add_parser("rubric", help="write the review rubric markdown")
    writer.add_argument("--out")
    writer.set_defaults(handler=rubric)
