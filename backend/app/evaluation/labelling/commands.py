import argparse
import asyncio
import uuid
from pathlib import Path
from typing import Any

from app.evaluation.cli import EvaluationRuntime, command_registry
from app.evaluation.labelling.guidelines import guidelines_markdown
from app.evaluation.labelling.importer import import_labels
from app.evaluation.labelling.pack import render_label_pack
from app.evaluation.labelling.population import EnginePopulationSource
from app.evaluation.labelling.render import render_outlier_eval
from app.evaluation.labelling.sampler import OutlierSampleDesigner
from app.evaluation.labelling.scorer import evaluate_labels
from app.evaluation.labelling.store import LabelStore
from app.evaluation.labelling.universe import clients_by_channel
from app.evaluation.packs.ids import batch_uuid
from app.evaluation.reporting.markdown import write_report
from app.services.analytics.settings import AnalyticsEngineSettings


def packs_dir(runtime: EvaluationRuntime) -> Path:
    # packs hold api data and expire; they live beside the freeze bundles, never in docs/
    return runtime.settings.evaluation.bundle_dir.parent / "eval_packs"


async def export(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    settings = runtime.settings
    container = runtime.container
    now = container.clock.now()
    population = await EnginePopulationSource(container, args.client).load(now)
    if not population.videos:
        print("no videos with a detector ratio; run an analysis for the demo clients first")
        return 1
    seed = args.seed if args.seed is not None else settings.evaluation.seed
    design = OutlierSampleDesigner(settings.evaluation.labelling).design(population, seed, batch_uuid(now))
    async with container.session_factory() as session, session.begin():
        await LabelStore(session).save_design(design, population, settings.policy.retention_days)
    out = Path(args.out) if args.out else packs_dir(runtime)
    out.mkdir(parents=True, exist_ok=True)
    for labeller in args.labeller:
        html = render_label_pack(
            design,
            population,
            participant=labeller,
            seed=seed,
            batch_size=settings.evaluation.labelling.pack_batch_size,
            retention_days=settings.policy.retention_days,
            now=now,
        )
        path = out / f"label-pack-{str(design.batch_id)[:8]}-{labeller}.html"
        path.write_text(html, encoding="utf-8")
        print(f"wrote {path}")
    print(f"batch {design.batch_id}: {len(design.tasks)} tasks")
    for note in design.notes:
        print(f"note: {note}")
    return 0


async def import_file(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    container = runtime.container
    batch_id = uuid.UUID(args.batch)
    async with container.session_factory() as session, session.begin():
        store = LabelStore(session)
        tasks = await store.tasks(batch_id)
        if not tasks:
            print(f"batch {batch_id} has no tasks")
            return 1
        result = import_labels(
            await asyncio.to_thread(Path(args.file).read_text, encoding="utf-8"),
            labeller=args.labeller,
            round_no=args.round,
            known_task_ids={t.task_id for t in tasks},
            labelled_at=container.clock.now(),
        )
        await store.save_labels(result.labels)
    print(f"imported {len(result.labels)} labels ({result.skipped} unanswered rows skipped)")
    return 0


async def score(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    settings = runtime.settings
    container = runtime.container
    async with container.session_factory() as session, session.begin():
        store = LabelStore(session)
        batch_id = uuid.UUID(args.batch) if args.batch else await store.latest_batch_id()
        if batch_id is None:
            print("no labelling batch exists yet")
            return 1
        tasks = await store.tasks(batch_id)
        labels = await store.labels([t.task_id for t in tasks])
        threshold = AnalyticsEngineSettings.from_settings(settings).outliers.outlier_ratio
        result, gold = evaluate_labels(
            batch_id,
            tasks,
            labels,
            settings.evaluation.labelling,
            threshold=threshold,
            seed=settings.evaluation.seed,
            clients_by_channel=await clients_by_channel(session),
        )
        await store.save_gold(list(gold.values()))
        await store.save_result(result)
    out = Path(args.out) if args.out else settings.evaluation.docs_dir / "outlier-eval.md"
    for path in write_report(out, render_outlier_eval(result), result.model_dump(mode="json")):
        print(f"wrote {path}")
    return 0


async def guidelines(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    out = Path(args.out) if args.out else runtime.settings.evaluation.docs_dir / "labelling-guidelines.md"
    for path in write_report(out, guidelines_markdown()):
        print(f"wrote {path}")
    return 0


@command_registry.register("labels")
def register(groups: Any) -> None:
    parser = groups.add_parser("labels", help="outlier labelling packs, label import and precision/recall")
    commands = parser.add_subparsers(dest="command", required=True)
    exporter = commands.add_parser(
        "export", help="sample 100 + shorts videos and write one pack per labeller"
    )
    exporter.add_argument("--labeller", action="append", required=True, help="repeat for each labeller")
    exporter.add_argument("--client", action="append", type=uuid.UUID, help="limit to these clients")
    exporter.add_argument("--seed", type=int)
    exporter.add_argument("--out", help="directory for the html packs")
    exporter.set_defaults(handler=export)
    importer = commands.add_parser("import", help="import a pack's exported csv")
    importer.add_argument("--batch", required=True)
    importer.add_argument("--labeller", required=True)
    importer.add_argument("--round", type=int, default=1, help="1 for primary labels, 2+ for adjudication")
    importer.add_argument("file")
    importer.set_defaults(handler=import_file)
    scorer = commands.add_parser("score", help="agreement, gold labels, weighted precision/recall, θ sweep")
    scorer.add_argument("--batch")
    scorer.add_argument("--out")
    scorer.set_defaults(handler=score)
    guide = commands.add_parser("guidelines", help="write the labelling guidelines markdown")
    guide.add_argument("--out")
    guide.set_defaults(handler=guidelines)
