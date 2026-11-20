import argparse
from pathlib import Path
from typing import Any
from uuid import UUID

from app.evaluation.backtest.render import render_backtest
from app.evaluation.backtest.runner import BacktestRunner, OutcomesNotMature
from app.evaluation.backtest.store import BacktestStore, ConfigChanged
from app.evaluation.cli import EvaluationRuntime, command_registry
from app.evaluation.reporting.markdown import write_report

REPORT_FILE = "backtest-report.md"


async def listed(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    async with runtime.container.session_factory() as session:
        rows = await BacktestStore(session).listed()
    for row in rows:
        config = row.config
        print(
            f"{row.eval_id}\t{row.status}\tclient={config.get('client_id')}\tT={config.get('as_of')}\t"
            f"hash={row.config_hash[:12]}"
        )
    return 0


async def score(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    container = runtime.container
    runner = BacktestRunner(container)
    try:
        report = await runner.score([UUID(e) for e in args.eval_id], container.clock.now())
    except (OutcomesNotMature, ConfigChanged, FileNotFoundError) as error:
        print(str(error))
        return 2
    out = Path(args.out) if args.out else runtime.settings.evaluation.docs_dir / REPORT_FILE
    for path in write_report(out, render_backtest(report), report.model_dump(mode="json")):
        print(path)
    print(report.headline)
    return 0


@command_registry.register("backtest")
def register(groups: Any) -> None:
    parser = groups.add_parser("backtest", help="forward backtest against copy-top and random arms")
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list", help="registered evaluations (one per backtest run)")
    list_parser.set_defaults(handler=listed)
    score_parser = commands.add_parser("score", help="collect post-T outcomes and write the report")
    score_parser.add_argument("--eval-id", action="append", required=True, help="repeat to pool clients")
    score_parser.add_argument("--out", help="defaults to docs/evaluation/backtest-report.md")
    score_parser.set_defaults(handler=score)
