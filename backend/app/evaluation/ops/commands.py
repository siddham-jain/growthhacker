import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from app.evaluation.cli import EvaluationRuntime, command_registry
from app.evaluation.ops.alerts import AlertEvaluator, default_notifiers
from app.evaluation.ops.render import render_ops_report
from app.evaluation.ops.report import OpsReportBuilder
from app.evaluation.ops.rollup import OpsRollupTask
from app.evaluation.reporting.markdown import write_report

REPORT_FILE = "ops-report.md"


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def report(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    container = runtime.container
    end = _aware(args.to) if args.to else container.clock.now()
    start = _aware(args.start) if args.start else end - timedelta(days=args.days)
    builder = OpsReportBuilder(container.session_factory, container.settings, container.clock)
    clients = [UUID(args.client)] if args.client else None
    built = await builder.build(start, end, client_ids=clients)
    out = Path(args.out) if args.out else runtime.settings.evaluation.docs_dir / REPORT_FILE
    for path in write_report(out, render_ops_report(built), built.model_dump(mode="json")):
        print(path)
    return 0


async def rollup(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    container = runtime.container
    task = OpsRollupTask(container.session_factory, container.settings, container.clock)
    day = date.fromisoformat(args.day) if args.day else task.yesterday()
    print(json.dumps(await task.roll(day)))
    return 0


async def alerts(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    container = runtime.container
    evaluator = AlertEvaluator(
        container.session_factory,
        container.settings,
        container.clock,
        default_notifiers(container.settings, container.http),
    )
    summary = await evaluator.evaluate(dry_run=args.dry_run)
    for item in summary.findings:
        print(f"{item.key}\t{item.severity or 'ok'}\t{item.value:g}\t(threshold {item.threshold:g})")
    print(json.dumps(summary.as_dict()))
    return 0


@command_registry.register("ops")
def register(groups: Any) -> None:
    parser = groups.add_parser("ops", help="quota, latency, cost and alert operations")
    commands = parser.add_subparsers(dest="command", required=True)
    report_parser = commands.add_parser("report", help="write the ops report markdown and json")
    report_parser.add_argument("--days", type=int, default=7)
    report_parser.add_argument("--from", dest="start", help="iso datetime, utc when naive")
    report_parser.add_argument("--to", help="iso datetime, utc when naive; defaults to now")
    report_parser.add_argument("--client", help="client uuid; project quota totals stay project-wide")
    report_parser.add_argument("--out", help="defaults to docs/evaluation/ops-report.md")
    report_parser.set_defaults(handler=report)
    rollup_parser = commands.add_parser("rollup", help="materialise one day into ops_daily_rollup")
    rollup_parser.add_argument("--day", help="YYYY-MM-DD, defaults to yesterday (utc)")
    rollup_parser.set_defaults(handler=rollup)
    alerts_parser = commands.add_parser("alerts", help="evaluate alert rules")
    alerts_parser.add_argument(
        "--dry-run", action="store_true", help="print findings without writing or notifying"
    )
    alerts_parser.set_defaults(handler=alerts)
